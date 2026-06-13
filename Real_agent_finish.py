# ====================================================
# AGENT IA COIFFEUR — VERSION GPT-4o OPTIMISÉE
# Avec intégration Supabase + configuration multi-salon
# BUG FIXES : tool calls, rigidité agent, dates relatives
# ====================================================

import os
import logging
import unicodedata
import json
import threading
from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client as TwilioClient
from apscheduler.schedulers.background import BackgroundScheduler
import openai
import uuid
import re
import pytz
from datetime import datetime, timedelta, date, timezone
from dotenv import load_dotenv
from supabase import create_client, Client
import requests

PARIS_TZ = pytz.timezone("Europe/Paris")

def now_paris() -> datetime:
    """Retourne l'heure actuelle dans le fuseau Europe/Paris."""
    return datetime.now(PARIS_TZ)

load_dotenv()
print("🔵 [BOOT 1/8] load_dotenv OK")

# ====================================================
# ⚙️ ARCHITECTURE MULTI-SALON — config chargée dynamiquement
# Les variables salon sont résolues à chaque appel via get_salon_config()
# ====================================================

SITE_CLIENT = "https://www.monsite-coiffure.com"

PRIX_HOMME_COUPE = {                             # À PERSONNALISER
    "normale":    15,
    "travaillee": 20,
}

PRIX_HOMME_COULEUR = {                           # À PERSONNALISER
    "classique":         30,
    "decoloration":      40,
    "meches_balayage":   30,
    "fantaisie":         30,
    "patine_ton_sur_ton": 20,
}

PRIX_FEMME_COUPE = {                             # À PERSONNALISER
    "brushing":       30,
    "carre":          25,
    "carré":          25,
    "frange":         10,
    "degrade":        40,
    "pixie":          35,
    "coupe_courte":   35,
    "longs_naturels": 30,
    "coupe":          30,
}

PRIX_FEMME_COULEUR = {                           # À PERSONNALISER
    "balayage":    60,
    "mèches":      60,
    "ombré hair":  70,
    "décoloration": 80,
    "ton sur ton": 30,
    "couleur":     30,
}

# ====================================================
# CONFIG TECHNIQUE — NE PAS MODIFIER
# ====================================================

print("🔵 [BOOT 2/8] Initialisation OpenAI…")
openai.api_key = os.getenv("API_KEY")
try:
    client_openai = openai.OpenAI(api_key=openai.api_key)
    print("🔵 [BOOT 2/8] OpenAI OK")
except Exception as e:
    print(f"⚠️  Erreur initialisation OpenAI: {e}")
    client_openai = None

print("🔵 [BOOT 3/8] Initialisation Supabase…")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("🔵 [BOOT 3/8] Supabase OK")
except Exception as _e_sb:
    print(f"⚠️  Supabase non initialisé : {_e_sb}")
    supabase = None

def _clean_env(key, default=""):
    return os.getenv(key, default).strip().strip('"').replace('\n', '').replace('\r', '').replace(' ', '')

print("🔵 [BOOT 4/8] Initialisation Twilio…")
TWILIO_ACCOUNT_SID = _clean_env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = _clean_env("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER      = _clean_env("TWILIO_NUMBER") or "+16066497918"
try:
    twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    print(f"🔵 [BOOT 4/8] Twilio OK — SID={TWILIO_ACCOUNT_SID[:8]}… token_len={len(TWILIO_AUTH_TOKEN)}")
except Exception as _e:
    print(f"⚠️  Twilio non initialisé : {_e}")
    twilio_client = None

BASE_URL = "https://barbershop-agent.onrender.com"

# Mettre à True pour réactiver l'envoi réel de SMS
SMS_ENABLED = False

# ====================================================
# MULTI-SALON — CACHE CONFIG DYNAMIQUE (TTL 60s)
# ====================================================
_salon_config_cache: dict = {}   # {twilio_number: {"data": dict, "ts": datetime}}
_coiffeurs_cache: dict   = {}    # {salon_id: {"data": list, "ts": datetime}}
_prestations_cache: dict = {}    # {salon_id: {"data": list, "ts": datetime}}
_CACHE_TTL = 60  # secondes

# Dernier salon synchronisé via /update-config — utilisé comme fallback par
# /sync-staff et /sync-services quand le payload ne contient pas de salon_id.
# Base44 appelle /update-config PUIS /sync-staff dans la même séquence.
_last_sync_salon: dict = {}  # {"salon_id": "...", "twilio_number": "...", "nom": "..."}

def _cache_valid(entry: dict | None) -> bool:
    if not entry:
        return False
    return (now_paris() - entry["ts"]).total_seconds() < _CACHE_TTL

def get_salon_config(to_number: str) -> dict | None:
    """Charge config du salon depuis Supabase (cache TTL 60s). Retourne None si inconnu."""
    entry = _salon_config_cache.get(to_number)
    if _cache_valid(entry):
        return entry["data"]
    if not supabase:
        return None
    try:
        res = supabase.table("salon").select("*").eq("twilio_number", to_number).limit(1).execute()
        if not res.data:
            print(f"⚠️ [SALON CONFIG] Aucun salon pour {to_number}")
            return None
        s = res.data[0]
        jours = s.get("jours_ouverts") or []
        if isinstance(jours, str):
            try: jours = json.loads(jours)
            except Exception: jours = []
        pd = s.get("pause_debut") or s.get("break_start")
        pf = s.get("pause_fin")   or s.get("break_end")
        config = {
            "id":                s.get("id"),
            "nom":               s.get("nom") or "le salon",
            "twilio_number":     s.get("twilio_number") or to_number,
            "telephone":         s.get("telephone") or to_number,
            "adresse":           s.get("adresse") or "",
            "horaire_ouverture": s.get("horaire_ouverture") or "09:00",
            "horaire_fermeture": s.get("horaire_fermeture") or "18:00",
            "jours_ouverts":     jours if isinstance(jours, list) else ["mardi","mercredi","jeudi","vendredi","samedi"],
            "pause_debut":       str(pd)[:5] if pd else None,
            "pause_fin":         str(pf)[:5] if pf else None,
            "webhook_url":       s.get("webhook_url") or "",
            "app_salon_id":      s.get("app_salon_id") or "",
        }
        _salon_config_cache[to_number] = {"data": config, "ts": now_paris()}
        print(f"✅ [{config['nom']}] Config chargée | {config['horaire_ouverture']}-{config['horaire_fermeture']} | pause={config['pause_debut']}-{config['pause_fin']}")
        return config
    except Exception as e:
        print(f"❌ [SALON CONFIG] Erreur pour {to_number}: {e}")
        return None

def get_coiffeurs(salon_id: str) -> list:
    """Charge coiffeurs depuis employee (cache TTL 60s)."""
    entry = _coiffeurs_cache.get(salon_id)
    if _cache_valid(entry):
        return entry["data"]
    if not supabase:
        return []
    try:
        res = supabase.table("employee").select("*").eq("salon_id", salon_id).execute()
        coiffeurs = []
        for e in (res.data or []):
            nom = e.get("full_name") or e.get("name") or e.get("first_name") or ""
            if not nom:
                continue
            repos_raw = e.get("days_off") or e.get("jours_repos") or []
            if isinstance(repos_raw, str):
                try: repos_raw = json.loads(repos_raw)
                except Exception: repos_raw = []
            coiffeurs.append({
                "nom":         nom,
                "id":          e.get("id"),
                "specialites": _normaliser_specialites(e.get("specialties") or e.get("role")),
                "jours_repos": [j.strip().lower() for j in (repos_raw or []) if j],
                "heure_debut": e.get("work_start") or "09:00",
                "heure_fin":   e.get("work_end")   or "18:00",
            })
        _coiffeurs_cache[salon_id] = {"data": coiffeurs, "ts": now_paris()}
        print(f"✅ [COIFFEURS] salon_id={salon_id} | {len(coiffeurs)} coiffeurs")
        return coiffeurs
    except Exception as e:
        print(f"❌ [COIFFEURS] {e}")
        return []

def get_prestations(salon_id: str) -> list:
    """Charge prestations depuis service (cache TTL 60s)."""
    entry = _prestations_cache.get(salon_id)
    if _cache_valid(entry):
        return entry["data"]
    if not supabase:
        return []
    try:
        res = supabase.table("service").select("*").eq("salon_id", salon_id).execute()
        prestations = res.data or []
        _prestations_cache[salon_id] = {"data": prestations, "ts": now_paris()}
        print(f"✅ [PRESTATIONS] salon_id={salon_id} | {len(prestations)} prestations")
        return prestations
    except Exception as e:
        print(f"❌ [PRESTATIONS] {e}")
        return []

def invalidate_salon_cache(twilio_number: str = None, salon_id: str = None):
    """Invalide le cache pour forcer rechargement immédiat."""
    if twilio_number and twilio_number in _salon_config_cache:
        del _salon_config_cache[twilio_number]
    if salon_id:
        _coiffeurs_cache.pop(salon_id, None)
        _prestations_cache.pop(salon_id, None)
    print(f"🔄 [CACHE] Invalidé | twilio={twilio_number!r} | salon_id={salon_id!r}")

print("🔵 [BOOT 5/8] Création dossier audio…")
os.makedirs("audio", exist_ok=True)
print("🔵 [BOOT 5/8] Dossier audio OK")

# ====================================================
# TRACKING DES COÛTS OPENAI
# ====================================================
PRIX_INPUT_PER_MILLION = 2.50   # USD par million tokens input (GPT-4o)
PRIX_OUTPUT_PER_MILLION = 10.00  # USD par million tokens output (GPT-4o)
TAUX_EUR_USD = 0.92             # Conversion USD to EUR

# Variables de session (remises à zéro pour chaque appel)
session_tokens_input = 0
session_tokens_output = 0
session_tokens_total = 0
session_nb_echanges = 0
session_cout_usd = 0.0
session_cout_eur = 0.0

def calculer_cout(tokens_input: int, tokens_output: int) -> tuple:
    """Calcule le coût en USD et EUR."""
    cout_input = (tokens_input / 1_000_000) * PRIX_INPUT_PER_MILLION
    cout_output = (tokens_output / 1_000_000) * PRIX_OUTPUT_PER_MILLION
    cout_usd = cout_input + cout_output
    cout_eur = cout_usd * TAUX_EUR_USD
    return round(cout_usd, 6), round(cout_eur, 6)

def enregistrer_usage(salon_id: str = None, salon_nom: str = None,
                      twilio_number: str = None, tokens_input: int = 0,
                      tokens_output: int = 0, nb_echanges: int = 0,
                      appel_abouti: bool = False):
    """Enregistre l'usage OpenAI dans Supabase pour le reporting."""
    if not twilio_number or tokens_input == 0 and tokens_output == 0:
        return

    cout_usd, cout_eur = calculer_cout(tokens_input, tokens_output)
    mois = now_paris().strftime("%Y-%m")

    try:
        supabase.table("usage_logs").insert({
            "salon_id": salon_id,
            "salon_nom": salon_nom or "",
            "twilio_number": twilio_number,
            "mois": mois,
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "tokens_total": tokens_input + tokens_output,
            "cout_usd": cout_usd,
            "cout_eur": cout_eur,
            "nb_echanges": nb_echanges,
            "appel_abouti": appel_abouti,
        }).execute()
        print(f"📊 [USAGE] {tokens_input + tokens_output} tokens | "
              f"€{cout_eur:.4f} | {nb_echanges} échanges")
    except Exception as e:
        print(f"⚠️  [USAGE ERROR] {e}")

def rapport_mensuel(mois: str = None):
    """Génère un rapport des coûts par salon pour le mois."""
    if not mois:
        mois = now_paris().strftime("%Y-%m")

    try:
        result = supabase.table("usage_logs")\
            .select("*")\
            .eq("mois", mois)\
            .execute()

        logs = result.data or []
        if not logs:
            print(f"\n❌ Aucune donnée pour {mois}\n")
            return {}

        # Grouper par salon
        salons = {}
        for log in logs:
            nom = log.get("salon_nom") or log.get("twilio_number") or "Unknown"
            if nom not in salons:
                salons[nom] = {
                    "nb_appels": 0,
                    "tokens_total": 0,
                    "cout_eur": 0.0,
                    "cout_usd": 0.0,
                    "appels_aboutis": 0,
                    "nb_echanges": 0,
                }
            salons[nom]["nb_appels"] += 1
            salons[nom]["tokens_total"] += log.get("tokens_total", 0)
            salons[nom]["cout_eur"] += float(log.get("cout_eur", 0))
            salons[nom]["cout_usd"] += float(log.get("cout_usd", 0))
            salons[nom]["appels_aboutis"] += 1 if log.get("appel_abouti") else 0
            salons[nom]["nb_echanges"] += log.get("nb_echanges", 0)

        # Affichage
        print(f"\n{'='*60}")
        print(f"📊 RAPPORT USAGE OPENAI — {mois}")
        print(f"{'='*60}")
        total_eur = 0.0
        total_tokens = 0

        for nom, data in sorted(salons.items()):
            taux = (data["appels_aboutis"] / data["nb_appels"] * 100) if data["nb_appels"] > 0 else 0
            print(f"\n🏢 SALON : {nom}")
            print(f"   Appels         : {data['nb_appels']}")
            print(f"   Aboutis        : {data['appels_aboutis']} ({taux:.0f}%)")
            print(f"   Tokens         : {data['tokens_total']:,}")
            print(f"   Échanges       : {data['nb_echanges']}")
            print(f"   💰 Coût mois   : €{data['cout_eur']:.4f} (${data['cout_usd']:.4f})")
            total_eur += data['cout_eur']
            total_tokens += data['tokens_total']

        print(f"\n{'='*60}")
        print(f"💰 TOTAL OPENAI — {mois}")
        print(f"   Tokens totaux  : {total_tokens:,}")
        print(f"   Coût total     : €{total_eur:.4f}")
        print(f"{'='*60}\n")

        return salons

    except Exception as e:
        print(f"⚠️  [RAPPORT ERROR] {e}")
        return {}

print("🔵 [BOOT 6/8] Création application FastAPI…")
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
print("🔵 [BOOT 6/8] FastAPI OK")

@app.on_event("startup")
async def startup_event():
    """Boot multi-salon — pas de chargement global, config résolue dynamiquement par appel."""
    print("🚀 [STARTUP] Architecture multi-salon active — config chargée dynamiquement par appel")
    try:
        sync_appointment_columns()
        print("✅ [STARTUP] sync_appointment_columns OK")
    except Exception as _e:
        print(f"⚠️ [STARTUP] sync_appointment_columns : {_e}")
    # Compter les salons actifs
    try:
        if supabase:
            nb = len(supabase.table("salon").select("id").execute().data or [])
            print(f"✅ [STARTUP] {nb} salon(s) enregistré(s) — prêt à recevoir des appels")
    except Exception as _e:
        print(f"⚠️ [STARTUP] Erreur comptage salons : {_e}")

END_CALL_MESSAGE = "Merci pour votre appel. Bonne journée et à bientôt au salon."

MESSAGE_HORAIRES = "Le salon est ouvert du mardi au samedi."  # horaires réels dans salon config
MESSAGE_PRIX_BASE = "Les tarifs commencent à partir de 15 euros selon la prestation."

PRESTATIONS_DUREE_PLAGE = {
    "homme": {
        "coupe":        (20, 30),
        "couleur":      (60, 90),
        "coupe_couleur": (120, 140),
    },
    "femme": {
        "coupe":        (30, 45),
        "couleur":      (90, 120),
        "coupe_couleur": (180, 180),
    },
}

JOURS_FR = {
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
    "vendredi": 4, "samedi": 5, "dimanche": 6,
}

MOIS_FR = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3,
    "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
    "aout": 8, "août": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "decembre": 12, "décembre": 12,
}

NOMS_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
NOMS_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

# ====================================================
# GESTION DES CONVERSATIONS (historique + contexte client)
# ====================================================
conversation_history = {}  # {phone: [{"role": "user/assistant/tool", "content": "..."}]}
client_context = {}        # {phone: {"nom": "...", "client_id": "...", "prenom": "..."}}
derniere_activite = {}     # {phone: datetime} — pour nettoyage auto des historiques inactifs

def get_conversation_history(telephone: str):
    """Récupère l'historique de conversation pour ce numéro."""
    if telephone not in conversation_history:
        conversation_history[telephone] = []
    return conversation_history[telephone]

def add_to_history(telephone: str, role: str, content: str):
    """Ajoute un message à l'historique."""
    history = get_conversation_history(telephone)
    history.append({"role": role, "content": content})
    derniere_activite[telephone] = now_paris()

def nettoyer_historiques():
    """Supprime les historiques de conversations inactifs depuis plus de 2h."""
    maintenant = now_paris()
    a_supprimer = []
    for tel in list(conversation_history.keys()):
        if tel in derniere_activite:
            if (maintenant - derniere_activite[tel]).seconds > 7200:
                a_supprimer.append(tel)
    for tel in a_supprimer:
        del conversation_history[tel]
        if tel in client_context:
            del client_context[tel]
        if tel in derniere_activite:
            del derniere_activite[tel]
    if a_supprimer:
        print(f"🧹 [CLEAN] {len(a_supprimer)} historiques supprimés")

def add_assistant_message_with_tools(telephone: str, content: str = None, tool_calls: list = None):
    """Ajoute un message assistant avec tool_calls."""
    history = get_conversation_history(telephone)
    msg = {"role": "assistant"}
    if content:
        msg["content"] = content
    else:
        msg["content"] = None
    if tool_calls:
        msg["tool_calls"] = tool_calls
    history.append(msg)

def add_tool_result(telephone: str, tool_call_id: str, result: str):
    """Ajoute le résultat d'un tool call."""
    history = get_conversation_history(telephone)
    history.append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": result
    })

def get_client_context(telephone: str):
    """Récupère le contexte client (nom, ID, etc)."""
    if telephone not in client_context:
        client_context[telephone] = {}
    return client_context[telephone]

def update_client_context(telephone: str, **kwargs):
    """Met à jour le contexte client."""
    ctx = get_client_context(telephone)
    ctx.update(kwargs)

def clean_messages(messages: list) -> list:
    """
    Nettoie l'historique pour éviter les messages orphelins.
    Règles OpenAI :
    1. Un message assistant avec tool_calls DOIT être suivi
       d'un message tool pour chaque tool_call_id
    2. Un message tool DOIT être précédé d'un assistant
       avec tool_calls
    """
    cleaned = []
    i = 0
    while i < len(messages):
        msg = messages[i]

        if msg.get('role') == 'assistant' and msg.get('tool_calls'):
            # Collecter tous les tool_call_ids attendus
            expected_ids = {tc['id'] for tc in msg['tool_calls']}

            # Chercher les tool results qui suivent
            tool_results = []
            j = i + 1
            while j < len(messages) and messages[j].get('role') == 'tool':
                tool_results.append(messages[j])
                j += 1

            # Vérifier que tous les IDs sont couverts
            found_ids = {tr.get('tool_call_id') for tr in tool_results}

            if expected_ids == found_ids and tool_results:
                # Tout est bon, ajouter le bloc complet
                cleaned.append(msg)
                cleaned.extend(tool_results)
                i = j
            else:
                # Bloc incomplet — ignorer complètement
                print(f"⚠️ [CLEAN] Bloc tool_calls incomplet ignoré "
                      f"(attendu: {expected_ids}, trouvé: {found_ids})")
                i = j if j > i + 1 else i + 1

        elif msg.get('role') == 'tool':
            # Message tool orphelin — ignorer
            print(f"⚠️ [CLEAN] Tool result orphelin ignoré : "
                  f"{msg.get('tool_call_id')}")
            i += 1
        else:
            cleaned.append(msg)
            i += 1

    return cleaned

# ====================================================
# SUPABASE — FONCTIONS CLIENT & RDV
# ====================================================

def load_salon_data(twilio_number: str = None):
    """Obsolète — invalidation de cache uniquement pour rétrocompatibilité."""
    pass

def load_all_salon_data():
    """Obsolète — architecture multi-salon : config chargée dynamiquement via get_salon_config()."""
    pass

def get_or_create_client(telephone: str) -> dict:
    """Cherche le client par son numéro. S'il existe → retourne sa fiche + enrichit le contexte."""
    try:
        result = supabase.table("clients")\
            .select("*")\
            .eq("telephone", telephone)\
            .execute()
        if result.data:
            client = result.data[0]
        else:
            nouveau = supabase.table("clients")\
                .insert({"telephone": telephone})\
                .execute()
            client = nouveau.data[0]
            update_client_context(telephone, client_nouveau=True)
        # Enrichir le contexte avec les RDVs passés (appointment)
        try:
            rdvs = supabase.table("appointment")\
                .select("*")\
                .eq("client_phone", telephone)\
                .order("date", desc=True).limit(5).execute().data or []
            derniere_visite = rdvs[0] if rdvs else None
            update_client_context(telephone,
                nb_visites=client.get("nb_visites", 0),
                derniere_visite=derniere_visite)
            if client.get("nom"):
                update_client_context(telephone,
                    prenom=client["nom"].split()[0],
                    client_id=client.get("id"),
                    nom=client.get("nom"))
        except Exception:
            pass
        return client
    except Exception as e:
        print(f"Erreur Supabase get_or_create_client: {e}")
        return {"id": None, "telephone": telephone, "nom": None, "nb_visites": 0}

def get_salon_by_twilio(twilio_number: str) -> dict | None:
    """Identifie le salon via son numéro Twilio (table Salon, colonne twilio_number)."""
    try:
        result = supabase.table("salon").select("*")\
            .eq("twilio_number", twilio_number).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"Erreur get_salon_by_twilio: {e}")
        return None


def mettre_a_jour_nom_client(client_id: str, nom: str):
    """Met à jour le nom du client dans Supabase."""
    try:
        supabase.table("clients")\
            .update({"nom": nom})\
            .eq("id", client_id)\
            .execute()
    except Exception as e:
        print(f"Erreur Supabase mettre_a_jour_nom_client: {e}")

def enregistrer_rdv(client_id, jour, heure, type_client,
                    prestation, coupe_detail, couleur_detail,
                    duree_max, prix, avec_shampoing=False, salon_id=None,
                    telephone=None, client_nom=None, salon: dict = None):
    """Enregistre un RDV dans Supabase, incrémente nb_visites, envoie SMS de confirmation."""
    # Résolution salon_id depuis salon dict en priorité, sinon requête Supabase
    if not salon_id and salon:
        salon_id = salon.get("id")
    if not salon_id and supabase:
        twilio_num_env = os.getenv("TWILIO_NUMBER", "")
        try:
            result = supabase.table("salon").select("id")\
                .eq("twilio_number", twilio_num_env).limit(1).execute()
            if result.data:
                salon_id = result.data[0]["id"]
        except Exception as e:
            print(f"⚠️ [RDV] Erreur salon_id : {e}")

    try:
        heure_fin = ajouter_minutes_hhmm(heure, 30)
        salon_id_eff = salon_id

        print(f"💾 [APPOINTMENT] salon_id={salon_id_eff} "
              f"client={client_nom} jour={jour} heure={heure}")

        appt_row = {
            "salon_id":       salon_id_eff,
            "client_name":    client_nom or telephone or "Inconnu",
            "client_phone":   telephone or "",
            "status":         "confirme",
            "date":           jour,
            "time":           heure + ":00" if len(heure) == 5 else heure,
            "heure_fin":      heure_fin + ":00" if len(heure_fin) == 5 else heure_fin,
            "service":        prestation,
            "staff_name":     coupe_detail or "",
            "coupe_detail":   coupe_detail or "",
            "type_client":    type_client or "homme",
            "avec_shampoing": bool(avec_shampoing),
            "price":          prix or 0,
            "source":         "agent_vocal",
            "rappel_envoye":  False,
            "created_at":     datetime.now(timezone.utc).isoformat(),
            "notes":          json.dumps({"source": "agent_vocal"}),
        }

        appt_result = supabase.table("appointment").insert(appt_row).execute()
        rdv_id = appt_result.data[0]["id"] if appt_result.data else None
        print(f"✅ [APPOINTMENT] Inséré id={rdv_id}")

        if client_id:
            client_row = supabase.table("clients")\
                .select("nb_visites")\
                .eq("id", client_id)\
                .execute().data
            if client_row:
                supabase.table("clients").update({
                    "nb_visites": client_row[0]["nb_visites"] + 1
                }).eq("id", client_id).execute()

        # SMS de confirmation immédiat
        if telephone and telephone not in ("console_test",):
            send_sms_confirmation(
                telephone=telephone,
                client_nom=client_nom,
                prestation=prestation,
                jour=jour,
                heure=heure,
                rdv_id=rdv_id,
                client_id=client_id,
                coiffeur=coupe_detail or None,
                salon=salon,
            )
        return rdv_id

    except Exception as e:
        print(f"Erreur Supabase enregistrer_rdv: {e}")
        import traceback
        traceback.print_exc()
        return None

def sync_appointment_columns():
    """Vérifie les colonnes disponibles dans la table appointment (debug)."""
    if not supabase:
        print("⚠️ [APPOINTMENT] Supabase non initialisé")
        return
    try:
        result = supabase.table("appointment")\
            .select("*").limit(1).execute()
        colonnes = list(result.data[0].keys()) if result.data else "table vide"
        print(f"📋 [APPOINTMENT] Colonnes disponibles : {colonnes}")
    except Exception as e:
        print(f"⚠️ [APPOINTMENT] Erreur lecture colonnes : {e}")

def est_creneau_disponible(jour: str, heure: str) -> bool:
    """Vérifie la disponibilité d'un créneau dans appointment (heure exacte)."""
    try:
        time_sql = heure + ":00" if len(heure) == 5 else heure
        result = supabase.table("appointment")\
            .select("id")\
            .eq("date", jour)\
            .eq("time", time_sql)\
            .neq("status", "cancelled")\
            .neq("status", "annule")\
            .execute()
        return len(result.data) == 0
    except Exception as e:
        print(f"Erreur Supabase est_creneau_disponible: {e}")
        return True

def est_creneau_disponible_v2(jour: str, heure: str, coiffeur: str = None,
                               coiffeurs: list = None, salon_id: str = None) -> dict:
    """
    Vérification étendue : appointment, fenêtre chevauchement 30min, per-coiffeur.
    Retourne : {"disponible": bool, "coiffeurs_libres": list[str], "rdvs_trouves": int}
    """
    coiffeurs = coiffeurs or []
    rdvs_trouves = 0
    def _norm(s: str) -> str:
        return (s or "").strip().lower()

    coiffeurs_pris: set[str] = set()
    try:
        heure_min = parse_hhmm_en_minutes(heure)

        q_ap = supabase.table("appointment")\
            .select("time, staff_name")\
            .eq("date", jour).neq("status", "cancelled")
        if salon_id:
            q_ap = q_ap.eq("salon_id", salon_id)
        if coiffeur:
            q_ap = q_ap.eq("staff_name", coiffeur)
        res_ap = q_ap.execute()
        for appt in (res_ap.data or []):
            try:
                t_raw = (appt.get("time") or "")[:5]
                if not t_raw:
                    continue
                t_min = parse_hhmm_en_minutes(t_raw)
                duree_appt = 30
                if t_min <= heure_min < t_min + duree_appt:
                    rdvs_trouves += 1
                    if appt.get("staff_name"):
                        coiffeurs_pris.add(_norm(appt["staff_name"]))
            except Exception:
                pass

    except Exception as e:
        print(f"⚠️ [DISPO] Erreur vérification étendue : {e}")
        return {"disponible": True, "coiffeurs_libres": [c["nom"] for c in coiffeurs], "rdvs_trouves": 0}

    _jour_norm_v2 = None
    if jour:
        try:
            _d2 = datetime.strptime(jour, "%Y-%m-%d").date()
            _jour_norm_v2 = NOMS_JOURS[_d2.weekday()].lower()
        except Exception:
            pass

    def _travaille(c: dict) -> bool:
        if not _jour_norm_v2:
            return True
        repos = [j.strip().lower() for j in (c.get("jours_repos") or [])]
        return _jour_norm_v2 not in repos if repos else True

    coiffeurs_libres = [
        c["nom"] for c in coiffeurs
        if _norm(c["nom"]) not in coiffeurs_pris and _travaille(c)
    ]

    if coiffeur:
        _c_obj = next((c for c in coiffeurs if _norm(c["nom"]) == _norm(coiffeur)), None)
        coiffeur_en_rdv   = _norm(coiffeur) in coiffeurs_pris
        coiffeur_en_repos = _c_obj and not _travaille(_c_obj)
        coiffeur_pris = coiffeur_en_rdv or coiffeur_en_repos
        disponible = not coiffeur_pris
        _raison = "repos" if coiffeur_en_repos else ("RDV" if coiffeur_en_rdv else "libre")
        print(f"🔍 [DISPO] salon_id={salon_id} | coiffeur={coiffeur!r} | rdvs_trouves={rdvs_trouves} | raison={_raison} | statut={'occupé' if coiffeur_pris else 'libre'}")
    else:
        disponible = rdvs_trouves == 0 or bool(coiffeurs_libres)
        print(f"🔍 [DISPO] salon_id={salon_id} | coiffeur=any | rdvs_trouves={rdvs_trouves} | coiffeurs_pris={coiffeurs_pris} | statut={'occupé' if not disponible else 'libre'}")

    return {"disponible": disponible, "coiffeurs_libres": coiffeurs_libres, "rdvs_trouves": rdvs_trouves}

def get_rdv_client(telephone: str, salon_id: str = None) -> list:
    """Récupère les RDV à venir d'un client depuis appointment."""
    try:
        today = now_paris().date().isoformat()
        q = supabase.table("appointment")\
            .select("*")\
            .eq("client_phone", telephone)\
            .neq("status", "cancelled")\
            .neq("status", "annule")\
            .gte("date", today)\
            .order("date")
        if salon_id:
            q = q.eq("salon_id", salon_id)
        result = q.execute()
        return result.data or []
    except Exception as e:
        print(f"Erreur Supabase get_rdv_client: {e}")
        return []

# ====================================================
# TWILIO SMS — CONFIRMATION & RAPPELS
# ====================================================

NOMS_JOURS_SMS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
NOMS_MOIS_SMS  = ["janvier", "février", "mars", "avril", "mai", "juin",
                   "juillet", "août", "septembre", "octobre", "novembre", "décembre"]

def _format_date_sms(date_iso: str) -> str:
    """Convertit '2026-04-25' en 'vendredi 25 avril'."""
    try:
        d = datetime.strptime(date_iso, "%Y-%m-%d").date()
        return f"{NOMS_JOURS_SMS[d.weekday()]} {d.day} {NOMS_MOIS_SMS[d.month - 1]}"
    except Exception:
        return date_iso


def send_sms(to: str, body: str, from_number: str = None) -> tuple[bool, str | None]:
    """Envoie un SMS via Twilio. Retourne (succès, twilio_sid)."""
    if not SMS_ENABLED:
        logging.info(f"[SMS DÉSACTIVÉ] SMS non envoyé à {to}")
        return False, None
    if not twilio_client:
        print("⚠️  [SMS] Client Twilio non initialisé — SMS non envoyé.")
        return False, None
    _from = from_number or os.getenv("TWILIO_NUMBER", "")
    if to == _from:
        print(f"⚠️  [SMS] Numéro destinataire identique au numéro Twilio ({to}) — ignoré.")
        return False, None
    if not to or not to.startswith("+") or len(to) < 8:
        print(f"⚠️  [SMS] Numéro invalide : {to} — ignoré.")
        return False, None
    try:
        msg = twilio_client.messages.create(body=body, from_=_from, to=to)
        print(f"✅  [SMS] Envoyé à {to} — SID {msg.sid}")
        return True, msg.sid
    except Exception as e:
        print(f"❌  [SMS] Erreur envoi à {to} : {e}")
        return False, None


def save_rappel_sms(rdv_id: str | None, client_id: str | None,
                    telephone: str, message: str, statut: str,
                    twilio_sid: str | None = None):
    """Enregistre une entrée dans la table rappels_sms.
    Colonnes réelles : rdv_id, envoye_le, statut, message_texte, twilio_sid.
    """
    try:
        row = {
            "rdv_id":        rdv_id,
            "statut":        statut,
            "message_texte": message,
            "envoye_le":     datetime.now(timezone.utc).isoformat(),
        }
        if twilio_sid:
            row["twilio_sid"] = twilio_sid
        supabase.table("rappels_sms").insert(row).execute()
    except Exception as e:
        print(f"⚠️  [SMS] Erreur enregistrement rappels_sms : {e}")


def send_sms_confirmation(telephone: str, client_nom: str | None,
                          prestation: str, jour: str, heure: str,
                          rdv_id: str | None, client_id: str | None,
                          coiffeur: str | None = None, salon: dict = None):
    """SMS confirmation court — 1 segment (< 160 chars, 1 émoji)."""
    if rdv_id and supabase:
        try:
            if supabase.table("rappels_sms").select("id").eq("rdv_id", rdv_id).limit(1).execute().data:
                print(f"⚠️ [SMS CONF] Déjà envoyé pour rdv_id={rdv_id} — ignoré")
                return
        except Exception:
            pass
    try:
        _d = datetime.strptime(jour, "%Y-%m-%d").date()
        _jour_court = f"{NOMS_JOURS_SMS[_d.weekday()][:3].capitalize()} {_d.day} {NOMS_MOIS_SMS[_d.month - 1]}"
    except Exception:
        _jour_court = jour
    try:
        _hp = (heure or "").split(":")
        heure_str = f"{int(_hp[0])}h{_hp[1]}" if len(_hp) >= 2 else (heure or "")
    except Exception:
        heure_str = heure or ""
    nom_salon = (salon or {}).get("nom", "le salon")
    tel_salon  = (salon or {}).get("telephone", "")
    twilio_num = (salon or {}).get("twilio_number", "")
    lignes = [
        "RDV confirme",
        f"{prestation} le {_jour_court} a {heure_str}",
    ]
    if coiffeur:
        lignes.append(f"Coiffeur : {coiffeur}")
    lignes.append(f"{nom_salon} - {tel_salon or twilio_num}")
    message = "\n".join(lignes)
    print(f"📱 [SMS CONF] {len(message)} chars | {telephone}")
    ok, sid = send_sms(telephone, message, from_number=twilio_num or None)
    save_rappel_sms(rdv_id, client_id, telephone, message,
                    "envoye" if ok else "echec", twilio_sid=sid)



def send_stats_quotidiennes():
    """Calcule et log les stats d'appels du jour pour chaque salon (23h55)."""
    if not supabase:
        return
    try:
        auj = now_paris().date().isoformat()
        res = supabase.table("call_stats").select("*").gte("started_at", auj).execute()
        rows = res.data or []
        if not rows:
            print(f"📊 [STATS QUOTIDIENNES] Aucun appel enregistré aujourd'hui ({auj})")
            return
        total        = len(rows)
        nb_rdv       = sum(1 for r in rows if r.get("rdv_pris"))
        taux         = round(nb_rdv / total * 100, 1) if total else 0
        durees       = [r["duration_seconds"] for r in rows if r.get("duration_seconds")]
        duree_moy    = round(sum(durees) / len(durees)) if durees else 0
        nb_abandons  = sum(1 for r in rows if r.get("motif_echec") == "abandon")
        nb_silences  = sum(1 for r in rows if r.get("motif_echec") == "silence")
        nb_pas_dispo = sum(1 for r in rows if r.get("motif_echec") == "pas_de_dispo")
        nb_ferme     = sum(1 for r in rows if r.get("motif_echec") == "fermé")
        print(
            f"📊 [STATS {auj}] total_appels={total} | rdv_pris={nb_rdv} | taux={taux}% "
            f"| durée_moy={duree_moy}s | abandons={nb_abandons} | silences={nb_silences} "
            f"| pas_dispo={nb_pas_dispo} | fermé={nb_ferme}"
        )
    except Exception as _e:
        print(f"⚠️ [STATS QUOTIDIENNES] Erreur : {_e}")

# ── Scheduler — nettoyage historiques + stats quotidiennes ─────────────────
print("🔵 [BOOT 7/8] Démarrage APScheduler…")
try:
    scheduler = BackgroundScheduler(timezone="Europe/Paris")
    scheduler.add_job(nettoyer_historiques, "cron", minute=30,
                      id="nettoyage_historiques", replace_existing=True)
    scheduler.add_job(send_stats_quotidiennes, "cron", hour=23, minute=55,
                      id="stats_quotidiennes", replace_existing=True)
    scheduler.start()
    print("🔵 [BOOT 7/8] Scheduler OK — nettoyage :30 | stats 23h55")
except Exception as _e_sched:
    print(f"⚠️  [BOOT 7/8] Scheduler non démarré : {_e_sched}")

print("🟢 [BOOT 8/8] Module chargé — uvicorn prêt à écouter sur $PORT")


def annuler_rdv(client_id: str, rdv_id: str) -> bool:
    """
    Annule un RDV par son rdv_id uniquement.
    client_id ignoré — GPT peut passer un téléphone par erreur au lieu d'un UUID.
    """
    if not rdv_id:
        print("❌ [ANNULATION] rdv_id manquant")
        return False
    try:
        res_ap = supabase.table("appointment")\
            .update({"status": "annule"})\
            .eq("id", rdv_id)\
            .execute()
        ok = bool(res_ap.data)
        if ok:
            print(f"✅ [ANNULATION] appointment id={rdv_id} → status=annule")
        return ok
    except Exception as e:
        print(f"⚠️ [ANNULATION] appointment : {e}")
        return False

def _normaliser_specialites(raw) -> list:
    """Convertit specialites en liste de strings, quel que soit le format reçu."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if s]
    if isinstance(raw, str):
        # "Coupe, Barbe" ou "['Coupe', 'Barbe']" ou "Coupe"
        raw = raw.strip()
        if raw.startswith("["):
            try:
                import ast
                parsed = ast.literal_eval(raw)
                if isinstance(parsed, list):
                    return [str(s).strip() for s in parsed if s]
            except Exception:
                pass
        return [s.strip() for s in raw.split(",") if s.strip()]
    return []

def coiffeurs_competents(prestation: str, jour: str = None, coiffeurs: list = None) -> list:
    """
    Filtre coiffeurs selon la prestation ET optionnellement le jour de travail.
    - Si jour fourni : exclut les coiffeurs en repos ce jour-là.
    - Si jours_repos vide → coiffeur considéré disponible tous les jours.
    """
    coiffeurs = coiffeurs or []
    _jour_norm = None
    if jour:
        try:
            _d = datetime.strptime(jour, "%Y-%m-%d").date()
            _jour_norm = NOMS_JOURS[_d.weekday()].lower()
        except Exception:
            _jour_norm = jour.strip().lower()

    def _coiffeur_travaille_ce_jour(c: dict) -> bool:
        if not _jour_norm:
            return True
        repos = [j.strip().lower() for j in (c.get("jours_repos") or [])]
        if not repos:
            return True
        if _jour_norm in repos:
            print(f"🔍 [REPOS] {c['nom']} en repos le {_jour_norm} — exclu pour ce créneau")
            return False
        return True

    candidats = [c for c in coiffeurs if _coiffeur_travaille_ce_jour(c)]

    if not prestation:
        return candidats if candidats else list(coiffeurs)

    prest_norm = normaliser_texte(prestation)

    def _peut_faire(c: dict) -> bool:
        return any(prest_norm in normaliser_texte(s) or normaliser_texte(s) in prest_norm
                   for s in _normaliser_specialites(c.get("specialites")))

    competents = [c for c in candidats if _peut_faire(c)]

    if competents:
        return competents

    competents_globaux = [c for c in coiffeurs if _peut_faire(c)]
    if competents_globaux:
        return []
    return candidats if candidats else list(coiffeurs)

def get_coiffeurs_disponibles(jour: str, heure: str, duree: int = 45, coiffeurs: list = None) -> list:
    """Retourne la liste des coiffeurs disponibles à l'heure demandée."""
    coiffeurs = coiffeurs or []
    try:
        time_sql = heure + ":00" if len(heure) == 5 else heure
        rdvs = supabase.table("appointment").select("staff_name")\
            .eq("date", jour).eq("time", time_sql)\
            .neq("status", "cancelled").neq("status", "annule").execute()
        coiffeurs_pris = {(r.get("staff_name") or "").strip().lower() for r in (rdvs.data or [])}
        disponibles = [c for c in coiffeurs if c["nom"].strip().lower() not in coiffeurs_pris]
        return disponibles if disponibles else list(coiffeurs)
    except Exception as e:
        print(f"⚠️ Erreur disponibilité coiffeur : {e}")
        return list(coiffeurs)

def get_prochains_creneaux_disponibles(jour: str, heure_souhaitee: str, nb: int = 3,
                                        coiffeur: str = None, coiffeurs: list = None,
                                        salon: dict = None) -> list:
    """Retourne les nb prochains créneaux libres à partir de l'heure souhaitée, pour le coiffeur donné."""
    coiffeurs = coiffeurs or []
    _salon_id_pcc = (salon or {}).get("id")
    creneaux = []
    heure_courante = heure_souhaitee or (salon or {}).get("horaire_ouverture", "09:00")
    for _ in range(20):
        if heure_valide_format(heure_courante) and est_horaire_ouverture(heure_courante, salon=salon):
            _d = est_creneau_disponible_v2(jour, heure_courante, coiffeur=coiffeur or None,
                                            coiffeurs=coiffeurs, salon_id=_salon_id_pcc)
            if _d["disponible"]:
                creneaux.append(heure_courante)
                if len(creneaux) >= nb:
                    break
        heure_courante = ajouter_minutes_hhmm(heure_courante, 30)
        if not est_horaire_ouverture(heure_courante, salon=salon):
            break
    return creneaux

def get_services(salon_id: str = None) -> list:
    """Retourne la liste des services disponibles."""
    return ["coupe homme", "coupe femme", "couleur", "brushing", "permanente", "mise en plis", "lissage", "soin"]

# ====================================================
# UTILITAIRE : Convertir texte → voix naturelle (mp3)
# ====================================================
def tts_voice(message):
    """Convertit un message texte en voix MP3."""
    audio_id = str(uuid.uuid4()) + ".mp3"
    path = f"audio/{audio_id}"
    os.makedirs("audio", exist_ok=True)
    with open(path, "wb") as f:
        try:
            result = client_openai.audio.speech.create(
                model="tts-1",
                voice="shimmer",
                input=message,
            )
            audio_bytes = result.read() if hasattr(result, "read") else bytes(result)
            f.write(audio_bytes)
        except Exception as e:
            print(f"Erreur TTS: {e}")
    return path

# ====================================================
# UTILITAIRES : Texte, date, heure
# ====================================================
def normaliser_texte(texte):
    texte = (texte or "").lower()
    texte = unicodedata.normalize("NFD", texte)
    texte = "".join(ch for ch in texte if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", texte).strip()

def date_du_jour():
    return now_paris().date()

def format_date_longue(date_obj):
    return f"{NOMS_JOURS[date_obj.weekday()]} {date_obj.day} {NOMS_MOIS[date_obj.month - 1]} {date_obj.year}"

def corriger_annee_date(date_iso: str) -> str:
    """Corrige l'année d'une date ISO si elle ne correspond pas à l'année courante (ou suivante max)."""
    if not date_iso:
        return date_iso
    try:
        annee_courante = now_paris().year
        parts = date_iso.split("-")
        if len(parts) == 3:
            annee = int(parts[0])
            if annee < annee_courante or annee > annee_courante + 1:
                print(f"⚠️ [DATE] Année corrigée : {annee} → {annee_courante}")
                return f"{annee_courante}-{parts[1]}-{parts[2]}"
    except Exception:
        pass
    return date_iso

def get_next_weekday(jour_nom: str) -> str:
    """Retourne la date ISO (YYYY-MM-DD) du prochain jour de la semaine demandé."""
    jours = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
             "vendredi": 4, "samedi": 5, "dimanche": 6}
    jour_nom_clean = normaliser_texte(jour_nom).split()[0]  # "lundi prochain" → "lundi"
    cible = jours.get(jour_nom_clean)
    if cible is None:
        return (now_paris().date() + timedelta(days=1)).isoformat()
    aujourd_hui = now_paris().date()
    jours_a_ajouter = (cible - aujourd_hui.weekday()) % 7
    if jours_a_ajouter == 0:
        jours_a_ajouter = 7
    return (aujourd_hui + timedelta(days=jours_a_ajouter)).isoformat()

def parse_hhmm_en_minutes(hhmm):
    heures, minutes = hhmm.split(":")
    return int(heures) * 60 + int(minutes)

def heure_valide_format(hhmm):
    return bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", hhmm or ""))

def normaliser_heure(hhmm):
    if not hhmm:
        return None
    if re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", hhmm):
        heures, minutes = hhmm.split(":")
        return f"{int(heures):02d}:{minutes}"
    return None

def est_horaire_ouverture(hhmm, salon=None):
    if not heure_valide_format(hhmm):
        return False
    valeur = parse_hhmm_en_minutes(hhmm)
    ouv = (salon or {}).get("horaire_ouverture", "09:00")
    fer = (salon or {}).get("horaire_fermeture", "18:00")
    return parse_hhmm_en_minutes(ouv) <= valeur <= parse_hhmm_en_minutes(fer)

def est_jour_ouvrable(date_iso, salon=None):
    try:
        d = datetime.strptime(date_iso, "%Y-%m-%d").date()
    except ValueError:
        return False
    jours = (salon or {}).get("jours_ouverts") or ["mardi","mercredi","jeudi","vendredi","samedi"]
    return NOMS_JOURS[d.weekday()] in jours

def ajouter_minutes_hhmm(hhmm, minutes):
    heures, mins = hhmm.split(":")
    total = int(heures) * 60 + int(mins) + minutes
    total = total % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"

def format_plage_duree(duree_min, duree_max):
    if duree_min == duree_max:
        heures = duree_min // 60
        minutes = duree_min % 60
        if heures and minutes:
            return f"{heures}h{minutes:02d}"
        if heures:
            return f"{heures}h"
        return f"{minutes} min"
    return f"{duree_min} - {duree_max} min"

# ====================================================
# OPTIMISATION 2 : Gestion intelligente des dates relatives
# ====================================================
def parse_date_relative(texte_date: str) -> str:
    """
    OPTIMISATION 2 : Convertit une date relative en format YYYY-MM-DD
    - "demain" -> date de demain
    - "apres-demain" ou "après-demain" -> +2 jours
    - "mardi prochain" -> prochain mardi
    - "ce week-end" -> samedi
    - "en debut de semaine" -> mardi
    - "le plus tot possible" -> premier jour ouvrable
    """
    aujourd_hui = now_paris().date()
    texte = normaliser_texte(texte_date)

    # Demain
    if "demain" in texte and "apres" not in texte and "après" not in texte:
        return (aujourd_hui + timedelta(days=1)).isoformat()

    # Après-demain
    if "apres" in texte or "après" in texte:
        if "demain" in texte:
            return (aujourd_hui + timedelta(days=2)).isoformat()

    # Jours de la semaine
    for jour_fr, jour_num in JOURS_FR.items():
        if jour_fr in texte:
            # "prochain" ou "ce"
            if "prochain" in texte or "ce " in texte:
                # Trouver le prochain occurrence du jour
                jours_a_ajouter = (jour_num - aujourd_hui.weekday()) % 7
                if jours_a_ajouter == 0:
                    jours_a_ajouter = 7  # Si c'est aujourd'hui, prendre la semaine prochaine
                return (aujourd_hui + timedelta(days=jours_a_ajouter)).isoformat()
            else:
                # Première occurrence (même semaine si possible)
                jours_a_ajouter = (jour_num - aujourd_hui.weekday()) % 7
                if jours_a_ajouter == 0:
                    jours_a_ajouter = 7
                return (aujourd_hui + timedelta(days=jours_a_ajouter)).isoformat()

    # Week-end
    if "week" in texte or "fin de semaine" in texte:
        # Samedi
        jours_a_ajouter = (5 - aujourd_hui.weekday()) % 7
        if jours_a_ajouter == 0:
            jours_a_ajouter = 7
        return (aujourd_hui + timedelta(days=jours_a_ajouter)).isoformat()

    # Début de semaine
    if "debut" in texte or "début" in texte:
        # Mardi
        jours_a_ajouter = (1 - aujourd_hui.weekday()) % 7
        if jours_a_ajouter == 0:
            jours_a_ajouter = 7
        return (aujourd_hui + timedelta(days=jours_a_ajouter)).isoformat()

    # Le plus tôt possible
    if "tot" in texte or "tôt" in texte or "vite" in texte:
        # Premier jour ouvrable
        for i in range(1, 14):
            date_candidate = aujourd_hui + timedelta(days=i)
            if est_jour_ouvrable(date_candidate.isoformat()):
                return date_candidate.isoformat()

    # Si aucun match, retourner demain par défaut
    return (aujourd_hui + timedelta(days=1)).isoformat()

# ====================================================
# PROMPT SYSTÈME AMÉLIORÉ
# ====================================================
def build_system_prompt(ctx_key: str, telephone: str, salon: dict, coiffeurs: list, prestations: list) -> str:
    salon = salon or {}
    coiffeurs = coiffeurs or []
    prestations = prestations or []
    _maintenant_paris = now_paris()
    aujourd_hui = _maintenant_paris.date()
    heure_actuelle = _maintenant_paris.strftime("%H:%M")

    nom_salon       = salon.get("nom", "le salon")
    horaire_ouv     = salon.get("horaire_ouverture", "09:00")
    horaire_fer     = salon.get("horaire_fermeture", "18:00")
    jours_ouverts   = salon.get("jours_ouverts") or ["mardi","mercredi","jeudi","vendredi","samedi"]
    pause_debut     = salon.get("pause_debut")
    pause_fin       = salon.get("pause_fin")
    adresse_salon   = salon.get("adresse", "")
    tel_salon       = salon.get("telephone", "")

    ctx = get_client_context(ctx_key) if ctx_key else {}
    prenom_client = ctx.get("prenom", "")
    nb_visites = ctx.get("nb_visites", 0)
    humeur_client = ctx.get("humeur", "neutre")
    derniere_prestation = ""
    if ctx.get("derniere_visite"):
        derniere_prestation = ctx["derniere_visite"].get("prestation", "")

    shampoing_info = ""
    if ctx_key:
        if ctx.get("shampoing_repondu"):
            reponse_sh = "oui" if ctx.get("avec_shampoing") else "non"
            shampoing_info = f"\nSHAMPOING : déjà demandé et répondu ({reponse_sh}). NE PAS redemander.\n"

    rdv_ctx = {}
    if ctx_key:
        rdv_ctx = {
            "prestation": ctx.get("rdv_prestation", ""),
            "jour":       ctx.get("rdv_jour", ""),
            "heure":      ctx.get("rdv_heure", ""),
            "shampoing":  ("oui" if ctx.get("avec_shampoing") else "non") if ctx.get("shampoing_repondu") else "",
            "coiffeur":   ctx.get("rdv_coiffeur", ""),
            "prenom":     prenom_client,
        }
        if not rdv_ctx["prenom"]:
            hist = get_conversation_history(ctx_key)
            for msg in hist:
                if msg.get("role") == "user":
                    words = msg["content"].strip().split()
                    if 1 <= len(words) <= 3 and all(w.isalpha() for w in words):
                        rdv_ctx["prenom"] = words[0].capitalize()
                        update_client_context(ctx_key, prenom=rdv_ctx["prenom"])
                        break
        if not rdv_ctx["prestation"] and prestations:
            hist = get_conversation_history(ctx_key)
            noms_prest_norm = [(p.get("name", ""), normaliser_texte(p.get("name", ""))) for p in prestations]
            for msg in hist:
                content_norm = normaliser_texte(str(msg.get("content", "")))
                for nom_orig, nom_norm in noms_prest_norm:
                    if nom_norm and nom_norm in content_norm:
                        rdv_ctx["prestation"] = nom_orig
                        update_client_context(ctx_key, rdv_prestation=nom_orig)
                        break
                if rdv_ctx["prestation"]:
                    break

    rdv_ctx_non_vides = {k: v for k, v in rdv_ctx.items() if v}
    if rdv_ctx_non_vides:
        rdv_ctx_str = "\n".join(f"  {k}={v}" for k, v in rdv_ctx_non_vides.items())
        rdv_context_block = f"\nCONTEXTE RDV EN COURS :\n{rdv_ctx_str}\nTous ces éléments sont ACQUIS. Ne pas les redemander.\n"
        print(f"📋 [{nom_salon}] [CONTEXTE RDV] prestation={rdv_ctx.get('prestation') or '—'} | jour={rdv_ctx.get('jour') or '—'} | heure={rdv_ctx.get('heure') or '—'} | coiffeur={rdv_ctx.get('coiffeur') or '—'}")
    else:
        rdv_context_block = ""

    _jours_ouverts_lower = [j.lower() for j in jours_ouverts]
    _nom_auj = NOMS_JOURS[aujourd_hui.weekday()]
    _statut_auj = "OUVERT" if _nom_auj.lower() in _jours_ouverts_lower else "FERMÉ"
    _cal_lignes = []
    _d_iter = aujourd_hui
    for _ in range(21):
        _nom_j = NOMS_JOURS[_d_iter.weekday()]
        _statut_j = "OUVERT" if _nom_j.lower() in _jours_ouverts_lower else "FERMÉ"
        _suffix = " ← AUJOURD'HUI" if _d_iter == aujourd_hui else ""
        _cal_lignes.append(f"{_d_iter.isoformat()} = {_nom_j} ({_statut_j}){_suffix}")
        _d_iter += timedelta(days=1)
    _dates_ref = "\n".join(_cal_lignes)

    prompt = f"""Réponds TOUJOURS en maximum 2 phrases courtes. Jamais plus.
Tu es la réceptionniste vocale professionnelle du salon {nom_salon}.
AUJOURD'HUI : {aujourd_hui.isoformat()} = {_nom_auj} ({_statut_auj}) | Heure : {heure_actuelle}
Horaires : {horaire_ouv}-{horaire_fer}, {', '.join([j.capitalize() for j in jours_ouverts])}.{f" Pause déjeuner : {pause_debut}-{pause_fin}." if pause_debut and pause_fin else ""}
Adresse : {adresse_salon} | Tél : {tel_salon}

CALENDRIER — correspondance date ↔ jour de la semaine (calculé par Python, fiable à 100%) :
{_dates_ref}
⛔ RÈGLE ABSOLUE : NE JAMAIS calculer toi-même le jour depuis une date ISO. Lire UNIQUEMENT dans le CALENDRIER ci-dessus.
RÈGLE : si le client dit "aujourd'hui", "demain", "mardi prochain" ou "le 3", retrouve la date correspondante dans ce tableau.
Pour les tools (verifier_disponibilite, prendre_rdv) : utilise UNIQUEMENT les dates ISO de ce tableau.
{shampoing_info}{rdv_context_block}
RÈGLE ABSOLUE PRIORITAIRE :
Tu te concentres EXCLUSIVEMENT sur la prise de rendez-vous. Si le client parle d'autre chose, réponds uniquement : "Je suis uniquement disponible pour la prise de rendez-vous. Souhaitez-vous prendre un rendez-vous ?" Ignorer tout bruit de fond, mot isolé, ou phrase non liée à une réservation.

IMPORTANT : Maximum 2 phrases courtes. Maximum 25 mots par réponse. Direct et efficace.

OBJECTIF VITESSE — RÈGLE ABSOLUE :
Ton objectif est de prendre le RDV le plus vite possible. Zéro étape inutile. Zéro récapitulatif oral.
Dès le premier message du client, extrais TOUTES les informations disponibles en une seule fois : prestation, jour, heure, coiffeur.
Si le client donne tout en une phrase ("je veux un dégradé jeudi à 10h") → traite tout immédiatement sans reposer de questions.
Ne pose une question QUE si une information est vraiment manquante et impossible à déduire.
Dès que tu as prestation + jour + heure validés → appelle prendre_rdv directement. Pas de récapitulatif oral. Pas de "c'est bien cela ?".
Après prendre_rdv, dire UNIQUEMENT : "C'est confirmé ! Vous recevez un SMS de confirmation. À bientôt !"

RAPPEL CONTEXTE — RÈGLE ABSOLUE :
Le CONTEXTE RDV EN COURS contient les éléments déjà validés : prestation, jour, heure, coiffeur, shampoing.
Si le client modifie UN élément (ex : change la date), CONSERVER TOUS LES AUTRES éléments tels quels.
Ne jamais vider ni ignorer le CONTEXTE RDV EN COURS. Ne jamais redemander un élément déjà acquis.
Exemple : client change "lundi" pour "mardi" → garder prestation, heure, coiffeur, shampoing. Juste re-vérifier la dispo pour mardi à la même heure.

RÈGLES ABSOLUES :
1. Professionnelle, courtoise, efficace. Vouvoiement systématique.
2. Tu n'as pas de prénom. Tu ne dis JAMAIS que tu es une IA.
3. UNE seule question à la fois. Ne jamais redemander ce qui a déjà été dit.
4. Extraire TOUTES les infos du message avant de questionner.
5. TOUJOURS répondre en français, quelles que soient les erreurs techniques. Si un outil échoue, dire en français : "Je rencontre un problème technique, je prends quand même votre rendez-vous." Ne jamais basculer en anglais même en cas d'erreur.
6. Si on change de prestation en cours de flow, NE PAS redemander le jour, l'heure, le shampoing ou le coiffeur déjà confirmés. Reprendre le flow à l'étape de vérification disponibilité directement.
7. Si CLIENT RECONNU est présent dans le contexte, le prénom est déjà connu — ne jamais poser la question du prénom.
8. Si un élément du RDV pose problème (prestation indispo, créneau pris, coiffeur absent), ne redemander QUE l'élément problématique. Tous les autres éléments déjà confirmés dans CONTEXTE RDV EN COURS sont acquis et ne doivent jamais être redemandés.
9. Si le prénom apparaît déjà dans CONTEXTE RDV EN COURS ou dans l'historique, ne jamais le redemander.

RÈGLE ANTI-RÉPÉTITION :
- Le shampoing ne doit être demandé QU'UNE SEULE fois. Une fois répondu, ne plus jamais poser cette question.
- Si "shampoing" apparaît déjà dans l'historique, ne pas redemander.
- Même si le créneau change, ne pas redemander le shampoing.

STYLE DE RÉPONSE :
Avant chaque question, accuser réception de ce que le client vient de dire.
Client : "Une coupe homme" → "Très bien. Pour quel jour souhaitez-vous venir ?"
Client : "Demain à 14h" → "Parfait, je vérifie ce créneau."
Client : "Oui" → "Très bien. C'est à quel nom ?"
Formules : "Très bien." / "Parfait." / "Noté." / "Bien sûr." / "Entendu." / "D'accord."
JAMAIS passer directement à la question suivante sans accuser réception.

TON ET STYLE :
Professionnel et chaleureux. Formulations : "Très bien", "Parfait", "Je vérifie", "Je vous propose".
Pas d'expressions familières ("super !", "génial !").

RÈGLE EXTRACTION COMPLÈTE :
Dès le premier message, extrais TOUT ce qui est disponible simultanément : prestation, jour, heure, coiffeur.
→ Si le client donne prestation + jour + heure dans un seul message → appelle verifier_disponibilite immédiatement, sans reposer aucune question.
→ Si la prestation manque et que le reste est donné → demander UNIQUEMENT la prestation.
→ Si le jour manque et que la prestation est donnée → demander UNIQUEMENT le jour.
→ Si l'heure manque et que prestation + jour sont donnés → demander UNIQUEMENT l'heure.
→ Si le client dit "je veux un rendez-vous" sans aucune info → demander UNIQUEMENT : "Quelle prestation souhaitez-vous ?"
Ne jamais poser plus d'une question à la fois. Ne jamais redemander une info déjà donnée.

⚠️ RÈGLE ABSOLUE N°1 — INTERDICTION PHRASES D'ATTENTE :
Il est STRICTEMENT INTERDIT de dire "je vais vérifier", "je vérifie", "un instant", "laissez-moi vérifier", "je consulte", "je regarde", "permettez-moi" ou toute formule similaire.
Ces phrases sont INTERDITES. À la place, appelle IMMÉDIATEMENT le tool verifier_disponibilite sans rien dire.
Si tu as prestation + jour + heure → appelle verifier_disponibilite TOUT DE SUITE. Pas de texte intermédiaire.
⛔ RÈGLE ABSOLUE N°1b — SILENCE PENDANT L'APPEL D'UN OUTIL :
Quand tu appelles un outil (tool_call), NE DIS RIEN au client. N'inclus AUCUN texte dans ta réponse : ni "Je vérifie...", ni "Un instant...", ni "Pouvez-vous répéter ?". Ta réponse doit contenir UNIQUEMENT le tool_call, sans texte. Le texte sera généré APRÈS réception du résultat de l'outil.

⚠️ RÈGLE ABSOLUE N°2 — VÉRIFICATION DISPONIBILITÉ IMMÉDIATE :
Dès que prestation + jour + heure sont tous les trois connus (même si fournis dans des messages séparés) → appelle verifier_disponibilite IMMÉDIATEMENT dans ta prochaine action.
Ne jamais passer à l'étape shampoing, coiffeur ou prénom sans avoir d'abord appelé verifier_disponibilite.
Ne jamais confirmer ni récapituler sans avoir appelé verifier_disponibilite.

⚠️ RÈGLE ABSOLUE N°3 — PRENDRE_RDV INTERDIT SANS VÉRIFICATION :
Ne jamais appeler prendre_rdv sans avoir appelé verifier_disponibilite juste avant dans cet appel.
L'ordre est immuable : verifier_disponibilite → prendre_rdv.
Shampoing, coiffeur et prénom sont optionnels : les inclure s'ils sont déjà connus, sinon passer directement à prendre_rdv.
⛔ INTERDIT de dire "C'est confirmé", "confirmation", "SMS envoyé" ou toute phrase de confirmation AVANT d'avoir appelé l'outil prendre_rdv.
verifier_disponibilite = vérification seulement. prendre_rdv = confirmation. Ces deux outils sont OBLIGATOIRES et dans cet ordre.

⚠️ RÈGLE ABSOLUE N°4 — JAMAIS INVENTER L'HEURE :
Ne jamais supposer, inventer ni proposer une heure. Si l'heure n'est pas donnée par le client :
→ Demander OBLIGATOIREMENT : "À quelle heure souhaitez-vous venir ?"
→ Attendre sa réponse. Ne jamais appeler verifier_disponibilite sans l'heure donnée par le client.

⚠️ RÈGLE ABSOLUE N°5 — CRÉNEAU REFUSÉ (JOUR DE REPOS, SALON FERMÉ, OU PAUSE DÉJEUNER) :
Quand un créneau est refusé (jour de repos, salon fermé, ou pause déjeuner) : expliquer POURQUOI c'est refusé en une phrase courte, puis proposer immédiatement un créneau avant ou après la pause.
Exemples :
→ Jour de repos coiffeur : "Dimanche, Jean Stéphane et Tom sont en repos, ce jour n'est pas possible. Quel autre jour vous conviendrait ?"
→ Salon fermé : "Le salon est fermé le lundi. Nous sommes ouverts [jours ouverts]. Quel jour vous conviendrait ?"
→ Pause déjeuner : "Le salon est en pause déjeuner de [PAUSE_DEBUT] à [PAUSE_FIN]. Je peux vous proposer [PAUSE_DEBUT - 30 min] avant la pause, ou [PAUSE_FIN] après. Laquelle vous convient ?"
→ NE JAMAIS enchaîner automatiquement sur le jour suivant sans que le client l'ait demandé.

⚠️ RÈGLE ABSOLUE N°6 — PRENDRE_RDV DIRECT, PAS DE RÉCAPITULATIF :
Ne jamais faire de récapitulatif oral ("Je récapitule : ..."). Ne jamais demander "c'est bien cela ?".
Dès que prestation + jour + heure sont validés par verifier_disponibilite → appeler prendre_rdv directement.
Après prendre_rdv réussi, dire UNIQUEMENT : "C'est confirmé ! Vous recevez un SMS de confirmation. À bientôt !"

VALIDATION IMMÉDIATE — RÈGLE CENTRALE :
Chaque information mentionnée par le client est validée IMMÉDIATEMENT. Ne jamais accumuler prestation + jour + heure pour tout vérifier à la fin. Une info = une vérification = une réponse immédiate si problème.

▸ PRESTATION mentionnée → vérifier IMMÉDIATEMENT dans la liste des coiffeurs si quelqu'un a cette compétence.
  • Si aucun coiffeur ne fait cette prestation : "Je suis désolé, nous ne proposons pas cette prestation. Voici nos prestations : [liste]. Laquelle vous intéresse ?"
  • Si la prestation existe : accuser réception et demander le jour.

▸ JOUR mentionné → vérifier IMMÉDIATEMENT deux choses SANS appeler de tool (l'info est dans ton contexte) :
  1. Le salon est-il ouvert ce jour-là ? (consulter JOURS OUVERTS dans ton contexte)
  2. Si un coiffeur est déjà connu : travaille-t-il ce jour-là ? (consulter ses jours de repos dans ton contexte)
  • Salon fermé ce jour : "Le salon est fermé le [jour]. Nous sommes ouverts [jours ouverts]. Quel jour vous conviendrait ?"
  • Coiffeur en repos ce jour : "[Coiffeur] est en repos le [jour]. [Autre coiffeur] est disponible, ou souhaitez-vous un autre jour ?"
  • Jour valide : accuser réception et demander l'heure.

▸ HEURE mentionnée (avec prestation + jour déjà connus) → appeler verifier_disponibilite IMMÉDIATEMENT.
  • Hors horaires d'ouverture : "Nous sommes ouverts de [ouverture] à [fermeture]. À quelle heure souhaitez-vous venir ?"
  • Créneau occupé : présenter les alternatives proposées par verifier_disponibilite.
  • Créneau libre → appeler prendre_rdv IMMÉDIATEMENT. Pas de récapitulatif. Pas de confirmation.

▸ TOUT DONNÉ EN UNE PHRASE (prestation + jour + heure) → verifier_disponibilite immédiatement, puis si libre → prendre_rdv immédiatement. Zéro question intermédiaire.

Exemples de validation immédiate :
  Client : "Je veux une coupe + barbe" → IMMÉDIATEMENT : vérifier qui fait coupe+barbe → "Très bien. Pour quel jour ?"
  Client : "Dimanche" → IMMÉDIATEMENT : consulter JOURS OUVERTS → si fermé : "Le salon est fermé le dimanche. Nous sommes ouverts [jours]. Quel jour ?"
  Client : "14h" → IMMÉDIATEMENT : appeler verifier_disponibilite → si libre : appeler prendre_rdv → "C'est confirmé ! SMS envoyé. À bientôt !"
  Client : "Un dégradé jeudi à 10h" → verifier_disponibilite → prendre_rdv → "C'est confirmé ! SMS envoyé. À bientôt !" (1 seul échange)

FLOW PRISE DE RDV — RAPIDE ET DIRECT :
Cas idéal (tout donné en une phrase) :
  → verifier_disponibilite → prendre_rdv → "C'est confirmé ! Vous recevez un SMS. À bientôt !"

Cas partiel (infos manquantes) :
  1. Extraire tout ce qui est déjà dans le message du client.
  2. Valider immédiatement chaque info reçue (compétence coiffeur / salon ouvert / repos).
  3. Poser UNE SEULE question pour l'info manquante.
  4. Dès que prestation + jour + heure sont connus → appeler verifier_disponibilite.
  5. Si créneau libre → appeler prendre_rdv directement (pas de recap, pas de confirmation).
  6. Dire : "C'est confirmé ! Vous recevez un SMS de confirmation. À bientôt !"

Infos optionnelles (shampoing, coiffeur, prénom) : inclure si déjà connues dans le contexte, sinon ne pas les demander.
NE JAMAIS REDEMANDER UN ÉLÉMENT DÉJÀ ACQUIS.
Quand tu appelles verifier_disponibilite, transmets aussi le champ "jour_semaine" si le client a mentionné un nom de jour (ex: "jeudi").

MESSAGES D'ATTENTE — RÈGLE CRITIQUE :
Lorsque tu appelles un outil (verifier_disponibilite, prendre_rdv, etc.), NE PAS écrire de texte d'attente dans ta réponse. Le système injecte automatiquement un message d'attente avant ton résultat. Si tu écris aussi un message d'attente, il sera dit EN DOUBLE.
→ Répondre DIRECTEMENT avec le résultat de l'outil, sans préambule d'attente.

RÈGLE MODIFICATION EN COURS DE FLOW :
Si le client modifie une information déjà fournie (jour, heure, prestation, coiffeur) :
→ Accuser réception : "Très bien, je modifie pour [nouvelle valeur]." puis appeler verifier_disponibilite avec les nouvelles valeurs.
→ Ne jamais ignorer silencieusement le changement.
→ Ne jamais continuer sur l'ancienne valeur sans confirmation.

ANNULATION RDV — ÉTAPES OBLIGATOIRES DANS L'ORDRE STRICT :
1. Appeler get_rdv_client_actif avec telephone={telephone or "inconnu"} pour récupérer les RDVs du client.
2. Présenter le RDV trouvé et demander confirmation EXPLICITE : "Votre rendez-vous pour [prestation] le [date] à [heure] est bien enregistré. Souhaitez-vous vraiment l'annuler ?"
3. Attendre la réponse du client. NE PAS appeler annuler_rdv avant d'avoir reçu un "oui" explicite.
4. Si client confirme (oui / je confirme / oui annuler) → appeler annuler_rdv avec l'ID du RDV.
5. Confirmer : "Votre rendez-vous est annulé. Vous allez recevoir un SMS de confirmation."
RÈGLE ABSOLUE : Ne jamais dire "je procède à l'annulation" sans avoir appelé le tool annuler_rdv. L'annulation n'est effective que si le tool retourne un succès.
⚠️ RÈGLE ABSOLUE : Ne jamais dire "je vais récupérer", "je cherche", "un instant" ou toute phrase d'attente. Appeler IMMÉDIATEMENT get_rdv_client_actif dès que le client mentionne une annulation. Pas de texte intermédiaire.
IMPORTANT — numéro client : le numéro de téléphone de l'appelant est {telephone or "inconnu"}. Pour get_rdv_client_actif, passer TOUJOURS ce numéro ({telephone or "inconnu"}) et jamais le numéro du salon ({tel_salon}).
⛔ RÈGLE ABSOLUE ANNULATION — CONFIRMATION OBLIGATOIRE : Après avoir trouvé le RDV via get_rdv_client_actif et demandé confirmation au client, attends UNIQUEMENT "oui" ou "non". "Au revoir" sans "oui" explicite = le client ne veut PAS annuler → ne jamais annuler, répondre "Bien entendu, votre RDV est conservé. Bonne journée !" et laisser l'appel se terminer normalement.

CONSEILS :
Appeler demander_rappel_conseil puis : "Je transmets votre demande, un membre vous rappellera rapidement au [numéro]."

FIN DE JOURNÉE :
Si le client demande un RDV aujourd'hui en fin de journée, calcule le temps restant et propose uniquement les créneaux réalisables. Exemple : s'il est 16h30 et que le salon ferme à 18h, dis "Il nous reste peu de temps aujourd'hui, je peux vous proposer 17h00 ou 17h30 selon la prestation."

FIN D'APPEL :
Si client dit au revoir / merci au revoir / bonne journée / c'est tout merci : "Merci pour votre appel. Bonne journée et à bientôt."

RÈGLE PRÉNOM : Si le client est nouveau (première visite), demande son prénom AVANT tout. Tu ne peux pas appeler prendre_rdv sans client_nom rempli. Si prendre_rdv retourne une demande de prénom, pose la question au client et rappelle prendre_rdv avec son prénom.

"""

    # Coiffeurs
    if len(coiffeurs) == 0:
        prompt += "COIFFEUR : Aucun coiffeur enregistré. Ne pas mentionner de coiffeur.\n"
    elif len(coiffeurs) == 1:
        nom_unique = coiffeurs[0]["nom"]
        _repos_u = coiffeurs[0].get("jours_repos") or []
        _repos_str_u = f" | repos: {', '.join(_repos_u)}" if _repos_u else ""
        _h_u = f"{coiffeurs[0].get('heure_debut', horaire_ouv)}-{coiffeurs[0].get('heure_fin', horaire_fer)}"
        prompt += (f"COIFFEUR : Un seul coiffeur — {nom_unique}{_repos_str_u} | horaires: {_h_u}. "
                   f"Ne jamais demander de préférence. Assigner automatiquement {nom_unique}.\n")
    else:
        noms_c = ', '.join([c['nom'] for c in coiffeurs])
        lignes_coif = []
        for c in coiffeurs:
            specs  = c.get("specialites") or []
            repos  = c.get("jours_repos") or []
            h_deb  = c.get("heure_debut") or horaire_ouv
            h_fin  = c.get("heure_fin")   or horaire_fer
            _spec_s  = f"spécialités [{', '.join(specs)}]" if specs else "toutes prestations"
            _repos_s = f"repos: {', '.join(repos)}" if repos else "disponible tous les jours d'ouverture"
            lignes_coif.append(f"  - {c['nom']} : {_spec_s} | {_repos_s} | horaires: {h_deb}-{h_fin}")
        coif_block = "\n".join(lignes_coif)
        prompt += (
            f"COIFFEURS ET DISPONIBILITÉS :\n{coif_block}\n"
            f"RÈGLES COIFFEUR (dans l'ordre) :\n"
            f"1. Après verifier_disponibilite, utilise le résultat 'Coiffeur assigné automatiquement' si présent → ne jamais poser la question de préférence dans ce cas.\n"
            f"2. Si le résultat liste plusieurs coiffeurs compétents disponibles → poser EXACTEMENT : \"Avez-vous une préférence pour un coiffeur ?\"\n"
            f"   Ne pas citer les noms dans cette question.\n"
            f"   - Client dit non → assigner automatiquement le premier de la liste.\n"
            f"   - Client dit oui → demander \"Lequel ? Nous avons : {noms_c}.\"\n"
            f"3. Si le client demande un coiffeur en repos ce jour ou hors horaires → lui dire et proposer un autre créneau ou un autre coiffeur.\n"
            f"4. Si le client demande un coiffeur non compétent pour la prestation → répondre :\n"
            f"   \"[Nom] ne propose pas cette prestation, seul [coiffeur compétent] peut vous la réaliser. Je vous confirme avec [coiffeur compétent] ?\"\n"
        )

    # Prestations disponibles
    if prestations:
        noms_prest = list(dict.fromkeys([
            p.get("name", "").strip()
            for p in prestations
            if p.get("name", "").strip()
        ]))
        prompt += f"\nPRESTATIONS DISPONIBLES ({len(noms_prest)}) :\n"
        prompt += "\n".join([f"- {n}" for n in noms_prest])
        prompt += f"""

RÈGLES ABSOLUES SUR LES PRESTATIONS :
- Ne JAMAIS lister les prestations spontanément
- Les citer UNIQUEMENT si le client demande explicitement : "qu'est-ce que vous proposez ?", "quelles sont vos prestations ?", "vous faites quoi ?"
- Si client demande une prestation non disponible : "Nous ne proposons pas cette prestation. Souhaitez-vous que je vous liste ce que nous faisons ?"
- Si client demande "c'est tout ?" ou "vous avez autre chose ?" → citer TOUTES les prestations restantes, ne jamais dire au revoir
- Ne jamais confondre fin de liste avec fin d'appel
- Quand tu listes les prestations, utilise TOUJOURS le tool get_services pour avoir la liste complète et exacte en temps réel. Ne jamais réciter de mémoire.
"""
    else:
        prompt += '\nPRESTATIONS : Aucune prestation enregistrée.\nSi on demande les prestations, réponds : "Je n\'ai pas encore la liste des prestations disponibles. Je vous invite à nous appeler directement pour plus d\'informations."\n'

    # Humeur client
    if humeur_client == "pressé":
        prompt += "Client pressé : soyez très concis, allez droit au but.\n"
    elif humeur_client == "stressé":
        prompt += "Client stressé : soyez rassurant et patient.\n"

    # Client reconnu
    if prenom_client and nb_visites > 0:
        prompt += f'\nCLIENT RECONNU : {prenom_client} ({nb_visites} visite(s)).\n'
        if derniere_prestation:
            prompt += f'Proposer : "Souhaitez-vous à nouveau une {derniere_prestation} ?"\n'
    elif prenom_client:
        prompt += f"\nCLIENT CONNU : {prenom_client}.\n"

    print(f"🧠 [{nom_salon}] [PROMPT] Jours={jours_ouverts} | Coiffeurs={len(coiffeurs)} | Prestations={len(prestations)} | Humeur={humeur_client}")
    return prompt

# ====================================================
# FONCTIONS POUR GPT-4o (function calling)
# ====================================================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "prendre_rdv",
            "description": "Enregistre un rendez-vous pour le client",
            "parameters": {
                "type": "object",
                "properties": {
                    "jour": {"type": "string", "description": "Date au format YYYY-MM-DD (ex: 2026-04-20)"},
                    "heure": {"type": "string", "description": "Heure au format HH:MM (ex: 14:00)"},
                    "prestation": {"type": "string", "description": "Type de prestation (coupe, couleur, coupe_couleur, etc)"},
                    "type_client": {"type": "string", "description": "homme ou femme"},
                    "coiffeur": {"type": "string", "description": "Nom du coiffeur (optionnel)"},
                    "avec_shampoing": {"type": "boolean", "description": "true si le client souhaite un shampoing"},
                    "client_nom": {"type": "string", "description": "Prénom ou nom du client"},
                },
                "required": ["jour", "heure", "prestation", "type_client"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verifier_disponibilite",
            "description": "Vérifie si un créneau est disponible",
            "parameters": {
                "type": "object",
                "properties": {
                    "jour": {"type": "string", "description": "Date au format YYYY-MM-DD"},
                    "heure": {"type": "string", "description": "Heure au format HH:MM"},
                    "prestation": {"type": "string", "description": "Prestation souhaitée (optionnel)"},
                    "jour_semaine": {"type": "string", "description": "Jour de la semaine énoncé par le client (ex: 'jeudi'), pour vérifier la cohérence avec la date ISO"},
                },
                "required": ["jour", "heure"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "annuler_rdv",
            "description": "Annule un rendez-vous existant",
            "parameters": {
                "type": "object",
                "properties": {
                    "client_id": {"type": "string", "description": "ID du client"},
                    "rdv_id": {"type": "string", "description": "ID du rendez-vous à annuler"},
                },
                "required": ["client_id", "rdv_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_rdv_client_actif",
            "description": "Récupère les RDV à venir du client pour pouvoir les annuler",
            "parameters": {
                "type": "object",
                "properties": {
                    "telephone": {"type": "string", "description": "numéro du client"},
                },
                "required": ["telephone"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_services",
            "description": "Retourne la liste des services disponibles",
            "parameters": {
                "type": "object",
                "properties": {
                    "salon_id": {"type": "string", "description": "ID du salon (optionnel)"},
                },
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_client_info",
            "description": "Récupère les informations du client par téléphone",
            "parameters": {
                "type": "object",
                "properties": {
                    "telephone": {"type": "string", "description": "Numéro de téléphone du client"},
                },
                "required": ["telephone"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "demander_rappel_conseil",
            "description": "Envoie un SMS au coiffeur quand un client demande des conseils personnalisés (coloration, coupe conseillée, soin, etc.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "nom_client": {"type": "string", "description": "Nom du client si connu"},
                    "telephone_client": {"type": "string", "description": "Numéro de téléphone du client"},
                },
                "required": ["telephone_client"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rechercher_client_par_nom",
            "description": "Recherche un client par son prénom ou nom dans la base de données",
            "parameters": {
                "type": "object",
                "properties": {
                    "nom": {"type": "string", "description": "Prénom ou nom du client à rechercher"},
                },
                "required": ["nom"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verifier_coiffeur_disponible",
            "description": "Vérifie si un coiffeur est disponible et retourne les alternatives",
            "parameters": {
                "type": "object",
                "properties": {
                    "jour": {"type": "string", "description": "Date YYYY-MM-DD"},
                    "heure": {"type": "string", "description": "Heure HH:MM"},
                    "coiffeur_souhaite": {"type": "string", "description": "Nom du coiffeur souhaité (optionnel)"},
                },
                "required": ["jour", "heure"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "proposer_creneaux",
            "description": "Propose les 3 prochains créneaux disponibles à partir d'une heure souhaitée",
            "parameters": {
                "type": "object",
                "properties": {
                    "jour": {"type": "string", "description": "Date au format YYYY-MM-DD"},
                    "heure_souhaitee": {"type": "string", "description": "Heure souhaitée au format HH:MM"},
                },
                "required": ["jour", "heure_souhaitee"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ajouter_liste_attente",
            "description": "Ajoute le client en liste d'attente si le salon est complet",
            "parameters": {
                "type": "object",
                "properties": {
                    "jour_souhaite": {"type": "string", "description": "Date souhaitée YYYY-MM-DD"},
                    "prestation": {"type": "string", "description": "Prestation souhaitée"},
                    "client_nom": {"type": "string", "description": "Nom du client"},
                },
                "required": ["jour_souhaite", "prestation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "transfert_humain",
            "description": "Transfère l'appel vers un humain en envoyant un SMS d'alerte au salon",
            "parameters": {
                "type": "object",
                "properties": {
                    "raison": {"type": "string", "description": "Raison du transfert (ex: demande client, urgence, etc.)"},
                },
            }
        }
    },
]

def process_tool_call(tool_name: str, tool_input: dict, telephone: str,
                      ctx_key: str = None, salon: dict = None,
                      coiffeurs: list = None, prestations: list = None) -> str:
    """Exécute une fonction appelée par GPT-4o et retourne le résultat."""
    ctx_key    = ctx_key or telephone
    salon      = salon      or {}
    coiffeurs  = coiffeurs  or []
    prestations = prestations or []
    nom_salon   = salon.get("nom", "le salon")
    tel_salon   = salon.get("telephone", "")
    twilio_num  = salon.get("twilio_number", "")
    webhook_url = salon.get("webhook_url", "")
    app_salon_id = salon.get("app_salon_id", "")

    if tool_name == "prendre_rdv":
        jour = corriger_annee_date(tool_input.get("jour"))
        heure = tool_input.get("heure")
        prestation = tool_input.get("prestation", "coupe")
        type_client = tool_input.get("type_client", "homme")
        avec_shampoing = bool(tool_input.get("avec_shampoing", False))
        # Lire coiffeur depuis tool_input OU depuis le contexte (assigné par VALID 1)
        _ctx_avant = get_client_context(ctx_key)
        coiffeur_choisi = (
            tool_input.get("coiffeur") or _ctx_avant.get("rdv_coiffeur", "")
        ) or None
        update_client_context(ctx_key,
            rdv_jour=jour, rdv_heure=heure,
            rdv_coiffeur=coiffeur_choisi or "",
            avec_shampoing=avec_shampoing, shampoing_repondu=True)

        # Vérifier que la prestation existe (si liste chargée)
        if prestations:
            _prest_norm = normaliser_texte(prestation)
            prestation_valide = any(
                normaliser_texte(p.get("name", "")) in _prest_norm
                or _prest_norm in normaliser_texte(p.get("name", ""))
                for p in prestations
            )
            if not prestation_valide:
                update_client_context(ctx_key, rdv_prestation="")
                noms = ', '.join(p.get("name", "") for p in prestations)
                print(f"⚠️ [{nom_salon}] [PRENDRE_RDV] Prestation invalide '{prestation}'")
                return (
                    f"Prestation '{prestation}' non disponible. "
                    f"Prestations disponibles : {noms}. "
                    f"Quelle prestation souhaitez-vous ?"
                )

        # Prestation valide : mémoriser dans le contexte
        update_client_context(ctx_key, rdv_prestation=prestation)

        # Vérifier la disponibilité par coiffeur (pas de blocage global)
        _check_rdv = est_creneau_disponible_v2(jour, heure, coiffeur=coiffeur_choisi,
                                               coiffeurs=coiffeurs, salon_id=salon.get("id"))
        if not _check_rdv["disponible"]:
            print(f"❌ [PRENDRE_RDV] Créneau indisponible pour {coiffeur_choisi or 'tout coiffeur'} à {heure} le {jour}")
            return f"Créneau indisponible. Merci de vérifier."

        # Récupérer/créer le client
        client = get_or_create_client(telephone)
        client_id  = client.get("id")
        client_nom = client.get("nom")

        # Sauvegarder le nom depuis le contexte session si absent en base
        if not client_nom:
            ctx = get_client_context(ctx_key)
            client_nom = ctx.get("prenom") or ctx.get("nom")
        nom_fourni = tool_input.get("client_nom") or tool_input.get("prenom")
        if nom_fourni and not client_nom:
            client_nom = nom_fourni
        if not client_nom:
            print(f"⚠️ [{nom_salon}] [PRENDRE_RDV] client_nom absent → demande prénom")
            return "Avant de confirmer votre rendez-vous, pouvez-vous me donner votre prénom ?"
        if client_nom and client_id:
            mettre_a_jour_nom_client(client_id, client_nom)

        # Enregistrer le RDV (déclenche SMS de confirmation)
        _rdv_id_cree = enregistrer_rdv(
            client_id=client_id,
            jour=jour,
            heure=heure,
            type_client=type_client,
            prestation=prestation,
            coupe_detail=coiffeur_choisi,
            couleur_detail=None,
            duree_max=45,
            prix=30,
            avec_shampoing=avec_shampoing,
            telephone=telephone,
            client_nom=client_nom,
            salon=salon,
        )
        # Sauvegarder pour call_stats avant de vider le contexte RDV
        update_client_context(ctx_key,
            rdv_id_cree=_rdv_id_cree,
            last_rdv_prestation=prestation,
            last_rdv_coiffeur=coiffeur_choisi or "",
        )
        # Sauvegarder préférences dans la table clients
        if client_id and supabase:
            try:
                prefs = {"derniere_coupe": prestation,
                         "coiffeur_habituel": tool_input.get("coiffeur"),
                         "avec_shampoing": avec_shampoing}
                supabase.table("clients").update({"preferences": json.dumps(prefs)}).eq("id", client_id).execute()
            except Exception:
                pass
        # Message fidélité
        nb_v = client.get("nb_visites", 0) + 1
        fidelite = ""
        if nb_v == 5:
            fidelite = " C'est votre 5ème visite, vous bénéficiez d'une remise de 10% !"
        elif nb_v == 10:
            fidelite = " 10ème visite ! Une prestation offerte vous attend !"
        update_client_context(ctx_key, rdv_en_cours=False, rdv_pris=True,
                              rdv_prestation="", rdv_jour="", rdv_heure="", rdv_coiffeur="")

        # ── Notification webhook vers l'app Base44 — ARRIÈRE-PLAN ──────────────
        print(f"📡 [{nom_salon}] [WEBHOOK] url={webhook_url!r} | app_salon_id={app_salon_id!r}")
        if not webhook_url:
            print(f"❌ [{nom_salon}] [WEBHOOK] URL vide — sync ignorée")
        elif not app_salon_id:
            print(f"❌ [{nom_salon}] [WEBHOOK] app_salon_id vide — sync ignorée")
        else:
            # Normaliser jour/heure avant de capturer dans le thread
            _jour_wh = jour or ""
            _heure_wh = heure or ""
            try:
                if _jour_wh and not (_jour_wh.count("-") == 2 and len(_jour_wh) == 10):
                    _jour_wh = datetime.strptime(_jour_wh, "%d/%m/%Y").strftime("%Y-%m-%d")
            except Exception:
                pass
            try:
                if _heure_wh and "h" in _heure_wh.lower():
                    _hp = re.split(r"h", _heure_wh.lower())
                    _heure_wh = f"{int(_hp[0]):02d}:{int(_hp[1]) if len(_hp) > 1 and _hp[1].strip() else 0:02d}"
            except Exception:
                pass

            _payload_dict = {
                "event":            "rdv_created",
                "app_salon_id":     app_salon_id,
                "client_telephone": telephone,
                "client_nom":       client_nom,
                "prestation":       prestation,
                "jour":             _jour_wh,
                "heure":            _heure_wh,
                "coiffeur":         coiffeur_choisi or "",
                "avec_shampoing":   avec_shampoing,
                "source":           "agent",
            }
            print(f"📡 [{nom_salon}] [WEBHOOK] PAYLOAD | {json.dumps(_payload_dict)}")

            def _run_webhook_creation(
                _wh_url=webhook_url,
                _wh_payload=_payload_dict,
                _wh_rdv_id=_rdv_id_cree,
                _wh_supabase=supabase,
            ):
                import time as _time_wh
                import re as _re_wh
                for _tentative in range(3):
                    try:
                        _resp = requests.post(
                            _wh_url,
                            json=_wh_payload,
                            timeout=10,
                        )
                        _body = _resp.text[:2000]
                        if _resp.status_code == 200:
                            _m = _re_wh.search(
                                r'"appointment_id"\s*:\s*"([a-f0-9]{24})"',
                                _body
                            )
                            if _m:
                                _b44_id = _m.group(1)
                                _wh_supabase.table("appointment")\
                                    .update({"base44_id": _b44_id})\
                                    .eq("id", _wh_rdv_id)\
                                    .execute()
                                logging.info(
                                    f"✅ [WEBHOOK ASYNC] "
                                    f"base44_id={_b44_id} | "
                                    f"tentative={_tentative+1}"
                                )
                                return
                        logging.warning(
                            f"⚠️ [WEBHOOK ASYNC] "
                            f"tentative={_tentative+1} "
                            f"status={_resp.status_code}"
                        )
                    except Exception as _e_wh:
                        logging.warning(
                            f"⚠️ [WEBHOOK ASYNC] "
                            f"tentative={_tentative+1} "
                            f"erreur={_e_wh}"
                        )
                    if _tentative < 2:
                        _time_wh.sleep(2)
                logging.warning(
                    "❌ [WEBHOOK ASYNC] "
                    "base44_id non sauvegardé après 3 tentatives"
                )

            threading.Thread(
                target=_run_webhook_creation,
                daemon=False,
            ).start()
            logging.info(f"📡 [WEBHOOK ASYNC] Thread lancé | rdv={_rdv_id_cree}")

        # ── Notification fidélité Base44 agentRdvConfirmed — ARRIÈRE-PLAN ───────
        if _rdv_id_cree and app_salon_id:
            _prenom_fid = (client_nom or "").split()[0] if client_nom else ""
            _jour_fid = jour or ""
            _heure_fid = heure or ""
            try:
                if _jour_fid and not (_jour_fid.count("-") == 2 and len(_jour_fid) == 10):
                    _jour_fid = datetime.strptime(_jour_fid, "%d/%m/%Y").strftime("%Y-%m-%d")
            except Exception:
                pass
            try:
                if _heure_fid and "h" in _heure_fid.lower():
                    _hp_fid = re.split(r"h", _heure_fid.lower())
                    _heure_fid = f"{int(_hp_fid[0]):02d}:{int(_hp_fid[1]) if len(_hp_fid) > 1 and _hp_fid[1].strip() else 0:02d}"
            except Exception:
                pass
            _fid_payload = json.dumps({
                "client_telephone": telephone,
                "client_nom":       _prenom_fid,
                "prestation":       prestation,
                "jour":             _jour_fid,
                "heure":            _heure_fid,
                "coiffeur":         coiffeur_choisi or "",
                "appointment_id":   _rdv_id_cree,
                "salon_id":         app_salon_id,
            }).encode("utf-8")

            def _run_fidelite(
                _url="https://snb-software.com/api/functions/agentRdvConfirmed",
                _payload=_fid_payload,
                _prenom=_prenom_fid,
                _appt_id=_rdv_id_cree,
            ):
                import urllib.request as _req_fid
                try:
                    _req = _req_fid.Request(
                        _url, data=_payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with _req_fid.urlopen(_req, timeout=10) as _r:
                        print(f"📊 [FIDELITE] Points envoyés pour {_prenom} | "
                              f"appointment_id={_appt_id} | statut={_r.status}")
                except Exception as _ef:
                    print(f"⚠️ [FIDELITE] Échec silencieux : {_ef}")

            threading.Thread(target=_run_fidelite, daemon=True).start()

            # Lookup prix depuis prestations (champ price) — match exact normalisé
            _prest_norm_pts = normaliser_texte(prestation)
            _prix_pts = next(
                (p.get("price") or 0 for p in prestations
                 if normaliser_texte(p.get("name", "")) == _prest_norm_pts),
                0
            ) or 0

            _pts_payload = json.dumps({
                "client_telephone": telephone,
                "client_nom":       _prenom_fid,
                "prestation":       prestation,
                "jour":             _jour_fid,
                "heure":            _heure_fid,
                "coiffeur":         coiffeur_choisi or "",
                "appointment_id":   _rdv_id_cree,
                "app_salon_id":     app_salon_id,
                "total_price":      _prix_pts,
            }).encode("utf-8")

            def _run_points(
                _url="https://snb-software.com/api/functions/agentRdvPoints",
                _payload=_pts_payload,
                _prenom=_prenom_fid,
                _appt_id=_rdv_id_cree,
            ):
                import urllib.request as _req_pts
                import json as _json_pts
                try:
                    _req = _req_pts.Request(
                        _url, data=_payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with _req_pts.urlopen(_req, timeout=10) as _r:
                        _rb = _r.read().decode("utf-8", errors="replace")
                        try:
                            _rj = _json_pts.loads(_rb)
                            _pts_added = _rj.get("points_added", "?")
                            _pts_total = _rj.get("total", "?")
                        except Exception:
                            _pts_added, _pts_total = "?", "?"
                        print(f"✅ [FIDELITE] Points envoyés | client={_prenom} | "
                              f"points={_pts_added}")
                except Exception as _ep:
                    print(f"⚠️ [FIDELITE] Échec | erreur={_ep}")

            threading.Thread(target=_run_points, daemon=True).start()

        return f"RDV enregistré pour {jour} à {heure}.{fidelite}"

    elif tool_name == "verifier_disponibilite":
        jour = corriger_annee_date(tool_input.get("jour"))
        heure = tool_input.get("heure")
        # Mémoriser jour/heure/prestation dans le contexte RDV
        if jour: update_client_context(ctx_key, rdv_jour=jour)
        if heure: update_client_context(ctx_key, rdv_heure=heure)
        _prest_arg = tool_input.get("prestation")
        if _prest_arg: update_client_context(ctx_key, rdv_prestation=_prest_arg)

        # Vérifier que le jour correspond à un jour ouvert du salon + cohérence nom/date
        _jours_ouverts = salon.get("jours_ouverts") or ["mardi","mercredi","jeudi","vendredi","samedi"]
        if jour:
            try:
                _date_obj = datetime.strptime(jour, "%Y-%m-%d").date()
                _nom_jour_reel = NOMS_JOURS[_date_obj.weekday()].lower()
                _jours_ouverts_lower = [j.lower() for j in _jours_ouverts]

                _jour_client = (tool_input.get("jour_semaine") or "").lower().strip()
                if not _jour_client:
                    _hist_tel = get_conversation_history(ctx_key)
                    if _hist_tel:
                        _last_user = next(
                            (m.get("content", "") for m in reversed(_hist_tel) if m.get("role") == "user"), ""
                        )
                        for _jn in NOMS_JOURS:
                            if _jn.lower() in _last_user.lower():
                                _jour_client = _jn.lower()
                                break

                if _jour_client and _jour_client != _nom_jour_reel:
                    print(f"⚠️ [{nom_salon}] [DATE] Incohérence : client dit '{_jour_client}', date {jour} est un {_nom_jour_reel}")
                    _date_fmt = f"{_date_obj.day} {NOMS_MOIS[_date_obj.month-1]}"
                    if _nom_jour_reel not in _jours_ouverts_lower:
                        return (
                            f"Le {_date_fmt} est un {_nom_jour_reel} et non un {_jour_client}. "
                            f"De plus le salon est fermé le {_nom_jour_reel}. "
                            f"Jours d'ouverture : {', '.join([j.capitalize() for j in _jours_ouverts])}. "
                            f"Quelle autre date vous conviendrait ?"
                        )
                    return (
                        f"CORRECTION DATE : Le {_date_fmt} est un {_nom_jour_reel} et non un {_jour_client}. "
                        f"Dire au client : 'Le {_date_fmt} est un {_nom_jour_reel} et non un {_jour_client}. "
                        f"Souhaitez-vous bien le {_nom_jour_reel} {_date_fmt} ?'"
                    )

                if _nom_jour_reel not in _jours_ouverts_lower:
                    return (f"Le salon est fermé le {_nom_jour_reel}. "
                            f"Jours d'ouverture : {', '.join([j.capitalize() for j in _jours_ouverts])}.")
            except Exception as _e:
                print(f"⚠️ [{nom_salon}] [DATE] Vérif jour ouvert : {_e}")

        # Vérifier que l'heure est dans les horaires d'ouverture
        _horaire_ouv = salon.get("horaire_ouverture", "09:00")
        _horaire_fer = salon.get("horaire_fermeture", "18:00")
        _pause_deb   = salon.get("pause_debut")
        _pause_fin_s = salon.get("pause_fin")
        if heure:
            try:
                heure_min = parse_hhmm_en_minutes(heure)
                ouv_min = parse_hhmm_en_minutes(_horaire_ouv)
                ferm_min = parse_hhmm_en_minutes(_horaire_fer)

                if not (ouv_min <= heure_min <= ferm_min):
                    return (f"Indisponible - le salon est ouvert de "
                            f"{_horaire_ouv} à {_horaire_fer}.")

                if _pause_deb and _pause_fin_s:
                    try:
                        pause_deb_min = parse_hhmm_en_minutes(_pause_deb)
                        pause_fin_min = parse_hhmm_en_minutes(_pause_fin_s)
                        if pause_deb_min <= heure_min < pause_fin_min:
                            print(f"⏸️ [{nom_salon}] [PAUSE] heure={heure} dans pause {_pause_deb}-{_pause_fin_s} → refusé")
                            return (
                                f"Indisponible - le salon est en pause déjeuner "
                                f"de {_pause_deb} à {_pause_fin_s}. "
                                f"Disponible avant {_pause_deb} ou à partir de {_pause_fin_s}."
                            )
                    except Exception:
                        pass

                duree_prestation = tool_input.get("duree", 30) or 30
                if (heure_min + duree_prestation) > ferm_min:
                    return (f"Indisponible - pas assez de temps avant la fermeture "
                            f"à {_horaire_fer} pour une prestation de {duree_prestation} min.")

                # Créneau aujourd'hui dans moins de 2h : compter les créneaux restants
                _np = now_paris()
                aujourd_hui = _np.date().isoformat()
                if jour == aujourd_hui:
                    maintenant_min = _np.hour * 60 + _np.minute
                    if heure_min - maintenant_min < 120:
                        try:
                            heure_sql = heure + ":00" if len(heure) == 5 else heure
                            reste = supabase.table("appointment")\
                                .select("id")\
                                .eq("date", aujourd_hui)\
                                .neq("status", "cancelled")\
                                .neq("status", "annule")\
                                .gte("time", heure_sql)\
                                .execute()
                            nb_pris = len(reste.data) if reste.data else 0
                            slots_total = (parse_hhmm_en_minutes(_horaire_fer) - heure_min) // 30
                            slots_libres = max(0, slots_total - nb_pris)
                            if slots_libres <= 2:
                                print(f"⏰ [{nom_salon}] [DISPOS] Peu de créneaux restants aujourd'hui : {slots_libres}")
                        except Exception:
                            slots_libres = None
            except Exception:
                pass

        update_client_context(ctx_key, rdv_en_cours=True)

        # ── Étape 1 : quels coiffeurs sont pris à ce créneau ? ────────────────
        _coiffeur_demande = (
            tool_input.get("coiffeur") or get_client_context(ctx_key).get("rdv_coiffeur", "")
        ).strip()
        _dispo = est_creneau_disponible_v2(jour, heure, coiffeur=_coiffeur_demande or None,
                                            coiffeurs=coiffeurs, salon_id=salon.get("id"))
        _coiffeurs_libres = _dispo["coiffeurs_libres"]
        _noms_libres_norm = {c.strip().lower() for c in _coiffeurs_libres}

        # ── Étape 2 : filtrer par compétence prestation ET jour de repos ─────────
        prestation_ctx = tool_input.get("prestation") or get_client_context(ctx_key).get("rdv_prestation", "")
        competents = coiffeurs_competents(prestation_ctx, jour=jour, coiffeurs=coiffeurs) if coiffeurs else []

        # Coiffeurs compétents ET libres à ce créneau
        competents_libres = [c for c in competents if c["nom"].strip().lower() in _noms_libres_norm]

        _heure_fmt = heure or "?"
        _jour_fmt  = jour  or "?"

        # ── Étape 3 : coiffeur spécifique demandé par le client ───────────────
        if _coiffeur_demande:
            coiffeur_pris = not _dispo["disponible"]
            if coiffeur_pris:
                # Ce coiffeur est pris — y a-t-il un autre compétent libre ?
                _alt_competents = [c for c in competents_libres
                                   if c["nom"].strip().lower() != _coiffeur_demande.strip().lower()]
                if _alt_competents:
                    _alt = _alt_competents[0]["nom"]
                    print(f"❌ [DISPO] {_coiffeur_demande} pris à {heure} — alternative : {_alt}")
                    return (
                        f"Disponibilité : occupé pour {_coiffeur_demande} — est déjà pris à {_heure_fmt}. "
                        f"Mais {_alt} est disponible. "
                        f"Demander au client : '{_coiffeur_demande} n'est pas disponible à {_heure_fmt}. "
                        f"Souhaitez-vous prendre avec {_alt} ?'"
                    )
                print(f"❌ [DISPO] {_coiffeur_demande} pris à {heure} — aucune alternative compétente")
                return (
                    f"Disponibilité : occupé — {_coiffeur_demande} est déjà pris à {_heure_fmt}. "
                    f"Ne pas chercher automatiquement d'autres créneaux. "
                    f"Demander au client : '{_coiffeur_demande} n'est pas disponible à {_heure_fmt}. "
                    f"Souhaitez-vous un autre horaire ou un autre jour ?'"
                )

        # ── Étape 4 : pas de coiffeur spécifique — vérifier les compétents ────
        if prestation_ctx and coiffeurs:
            if not competents:
                _competents_tous = coiffeurs_competents(prestation_ctx, coiffeurs=coiffeurs)
                if _competents_tous:
                    _jours_dispo = sorted({
                        j for c in _competents_tous
                        for j in _jours_ouverts
                        if j.lower() not in [r.strip().lower() for r in (c.get("jours_repos") or [])]
                    })
                    _jours_str = ", ".join(j.capitalize() for j in _jours_dispo) or "les jours d'ouverture"
                    _nom_jour = NOMS_JOURS[datetime.strptime(jour, "%Y-%m-%d").date().weekday()].capitalize() if jour else "ce jour"
                    return (
                        f"Disponibilité : occupé — aucun coiffeur compétent pour {prestation_ctx} "
                        f"n'est disponible le {_nom_jour}. "
                        f"Jours disponibles pour cette prestation : {_jours_str}. "
                        f"Demander au client : 'Cette prestation n'est pas disponible le {_nom_jour}. "
                        f"Je peux vous proposer un rendez-vous le {_jours_str}.'"
                    )
                return (f"Disponibilité : libre. "
                        f"Aucun coiffeur ne propose '{prestation_ctx}' actuellement.")

            if len(competents) == 1:
                seul = competents[0]
                if seul["nom"].strip().lower() not in _noms_libres_norm:
                    print(f"❌ [{nom_salon}] [DISPO] Seul coiffeur compétent ({seul['nom']}) pris à {heure} pour {prestation_ctx}")
                    return (
                        f"Disponibilité : occupé — {seul['nom']} est le seul coiffeur compétent "
                        f"pour {prestation_ctx} et est déjà pris à {_heure_fmt}. "
                        f"Ne pas chercher automatiquement d'autres créneaux. "
                        f"Demander au client : '{seul['nom']} n'est pas disponible à {_heure_fmt}. "
                        f"Souhaitez-vous un autre horaire ou un autre jour ?'"
                    )
                update_client_context(ctx_key, rdv_coiffeur=seul["nom"])
                print(f"✅ [COIFFEUR] Assignation auto : {seul['nom']} pour {prestation_ctx} | statut=libre")
                return (f"Disponibilité : libre. "
                        f"Coiffeur assigné automatiquement : {seul['nom']}. "
                        f"Ne pas poser la question de préférence coiffeur.")

            else:
                # Plusieurs compétents : vérifier combien sont libres
                if not competents_libres:
                    noms_comp = ', '.join(c['nom'] for c in competents)
                    print(f"❌ [DISPO] Tous les coiffeurs compétents ({noms_comp}) sont pris à {heure} — statut=occupé")
                    return (
                        f"Disponibilité : occupé — tous les coiffeurs compétents pour {prestation_ctx} "
                        f"({noms_comp}) sont pris à {_heure_fmt}. "
                        f"Ne pas chercher automatiquement d'autres créneaux. "
                        f"Demander au client : 'Ce créneau est déjà pris. Souhaitez-vous un autre horaire ou un autre jour ?'"
                    )
                noms_libres = ', '.join(c['nom'] for c in competents_libres)
                print(f"✅ [DISPO] {len(competents_libres)} coiffeur(s) compétent(s) libre(s) à {heure} : {noms_libres} | statut=libre")
                return (f"Disponibilité : libre. "
                        f"Coiffeurs compétents pour {prestation_ctx} disponibles à {_heure_fmt} : {noms_libres}. "
                        f"Poser la question de préférence.")

        # Enrichir la réponse si peu de créneaux aujourd'hui (cas sans filtre prestation)
        try:
            _np2 = now_paris()
            if jour == _np2.date().isoformat():
                maintenant_min = _np2.hour * 60 + _np2.minute
                heure_min_v = parse_hhmm_en_minutes(heure)
                if heure_min_v - maintenant_min < 120:
                    slots_total = (parse_hhmm_en_minutes(_horaire_fer) - heure_min_v) // 30
                    if slots_total <= 2:
                        return f"Disponibilité : libre. Il ne reste que {slots_total} créneau(x) aujourd'hui."
        except Exception:
            pass

        return "Disponibilité : libre"

    elif tool_name == "annuler_rdv":
        # client_id ignoré — peut être un téléphone passé par erreur par GPT
        rdv_id = tool_input.get("rdv_id") or tool_input.get("id") or ""
        print(f"🗑️ [ANNULATION] rdv_id={rdv_id} tel={telephone} (client_id GPT ignoré)")

        if not rdv_id:
            return "Erreur : l'identifiant du rendez-vous est manquant. Impossible d'annuler."

        # ── Récupérer les détails du RDV AVANT annulation ─────────────────────
        _rdv_prestation   = ""
        _rdv_date_lisible = ""
        _rdv_heure_lisible = ""
        _rdv_coiffeur     = ""
        _rdv_client_name  = ""
        _base44_id_ann = None
        _date_iso = ""
        _time_raw = ""
        try:
            _r = supabase.table("appointment")\
                .select("service,date,time,staff_name,client_name,base44_id,notes")\
                .eq("id", rdv_id).execute()
            if _r.data:
                _d = _r.data[0]
                _rdv_prestation  = (_d.get("service") or "").strip()
                _rdv_coiffeur    = (_d.get("staff_name") or "").strip()
                _rdv_client_name = (_d.get("client_name") or "").strip()
                _base44_id_ann   = (_d.get("base44_id") or "").strip() or None
                if not _base44_id_ann:
                    try:
                        _notes_raw = _d.get("notes") or ""
                        _notes_obj = json.loads(_notes_raw) if isinstance(_notes_raw, str) and _notes_raw else {}
                        _base44_id_ann = (_notes_obj.get("base44_id") or "").strip() or None
                        if _base44_id_ann:
                            print(f"✅ [ANNULATION] base44_id récupéré depuis notes | {_base44_id_ann}")
                    except Exception:
                        pass
                _date_iso = (_d.get("date") or "").strip()
                if _date_iso:
                    try:
                        _dt = datetime.strptime(_date_iso, "%Y-%m-%d").date()
                        _rdv_date_lisible = f"{NOMS_JOURS_SMS[_dt.weekday()].capitalize()} {_dt.day} {NOMS_MOIS_SMS[_dt.month - 1]} {_dt.year}"
                    except Exception:
                        _rdv_date_lisible = _date_iso
                _time_raw = (_d.get("time") or "").strip()
                if _time_raw:
                    try:
                        _parts = _time_raw.split(":")
                        _h, _m = int(_parts[0]), _parts[1]
                        _rdv_heure_lisible = f"{_h}h{_m}"
                    except Exception:
                        _rdv_heure_lisible = _time_raw
        except Exception as _e:
            print(f"⚠️ [ANNULATION] Impossible de récupérer détails appointment : {_e}")

        # Fallback base44_id : recherche via API Base44 si toujours absent
        if not _base44_id_ann and webhook_url and _date_iso and _time_raw:
            try:
                import urllib.request as _urlreq_srch
                _srch_url = webhook_url.rsplit("/", 1)[0] + "/search"
                _srch_payload = json.dumps({
                    "app_salon_id": app_salon_id,
                    "client_telephone": telephone,
                    "jour": _date_iso,
                    "heure": _time_raw[:5],
                }).encode("utf-8")
                _srch_req = _urlreq_srch.Request(
                    _srch_url,
                    data=_srch_payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with _urlreq_srch.urlopen(_srch_req, timeout=5) as _sr:
                    if _sr.status == 200:
                        _sr_data = json.loads(_sr.read().decode("utf-8", errors="replace"))
                        _b44_found = (_sr_data.get("appointment_id") or "").strip() or None
                        if _b44_found:
                            _base44_id_ann = _b44_found
                            print(f"✅ [ANNULATION] base44_id récupéré depuis Base44 search | {_base44_id_ann}")
                            supabase.table("appointment").update({"base44_id": _base44_id_ann}).eq("id", rdv_id).execute()
            except Exception as _srch_e:
                print(f"⚠️ [ANNULATION] base44_id introuvable — annulé Supabase uniquement ({type(_srch_e).__name__})")

        logging.info(
            f"🔍 [ANNULATION] rdv={rdv_id} | "
            f"base44_id='{_base44_id_ann}' | "
            f"service={_rdv_prestation!r} | "
            f"date={_rdv_date_lisible!r} heure={_rdv_heure_lisible!r}"
        )

        if annuler_rdv(None, rdv_id):
            logging.info(f"✅ [ANNULATION] id={rdv_id} → status=annule")
            update_client_context(ctx_key, en_attente_confirmation_annulation=False, annulation_effectuee=True)
            # ── Construction SMS annulation complet ───────────────────────────
            ctx = get_client_context(ctx_key)
            _prenom_ann = ctx.get("prenom") or ""
            if not _prenom_ann and _rdv_client_name:
                _prenom_ann = _rdv_client_name.split()[0]

            # Fallbacks champs optionnels
            _prest_affichee  = _rdv_prestation or "votre prestation"
            _date_affichee   = _rdv_date_lisible or ""
            _heure_affichee  = _rdv_heure_lisible or ""

            print(
                f"📱 [SMS ANNULATION] Prénom={_prenom_ann!r} | prestation={_prest_affichee!r} "
                f"| jour={_date_affichee!r} | heure={_heure_affichee!r} "
                f"| coiffeur={_rdv_coiffeur!r} | tel={telephone}"
            )

            _ann_lignes = ["RDV annule"]
            if _date_affichee and _heure_affichee:
                _ann_lignes.append(
                    f"{_prest_affichee} le {_date_affichee} a {_heure_affichee}"
                )
            if _rdv_coiffeur:
                _ann_lignes.append(f"Coiffeur : {_rdv_coiffeur}")
            _ann_lignes.append(f"{nom_salon} - {tel_salon or twilio_num}")
            message_annulation = "\n".join(_ann_lignes)
            print(f"📱 [{nom_salon}] [SMS ANNUL] {len(message_annulation)} chars | {telephone}")

            ok_sms, _sid = send_sms(telephone, message_annulation, from_number=twilio_num or None)
            print(f"📱 [ANNULATION] SMS envoyé : ok={ok_sms}")

            # ── Webhook vers Base44 (annulation) ─────────────────────────────
            if _base44_id_ann:
                _ann_payload = {
                    "action": "cancelled",
                    "appointment_id": _base44_id_ann,
                    "app_salon_id": app_salon_id,
                    "source": "agent",
                }
                try:
                    _ann_resp = requests.post(
                        webhook_url,
                        json=_ann_payload,
                        timeout=5,
                    )
                    logging.info(
                        f"📡 [WEBHOOK ANNULATION] "
                        f"base44_id={_base44_id_ann} | "
                        f"status={_ann_resp.status_code}"
                    )
                except Exception as _ann_e:
                    logging.warning(f"⚠️ [WEBHOOK ANNULATION] {_ann_e}")
            else:
                logging.warning(
                    f"⚠️ [WEBHOOK ANNULATION] Skippé "
                    f"— base44_id vide pour {rdv_id}"
                )

            return "RDV annulé avec succès. SMS de confirmation envoyé au client."

        return (
            "Je suis désolé, une erreur technique s'est produite. "
            "Votre rendez-vous n'a pas été annulé. "
            "Veuillez rappeler pour que nous puissions vous aider."
        )

    elif tool_name == "get_rdv_client_actif":
        tel_gpt  = (tool_input.get("telephone") or "").strip()
        tel_reel = telephone
        if tel_gpt and tel_gpt != tel_reel and tel_gpt != tel_salon:
            tel = tel_gpt
        else:
            tel = tel_reel
        print(f"📋 [{nom_salon}] [RDV ACTIF] tel_gpt={tel_gpt!r} | tel_reel={tel_reel!r} | utilise={tel!r}")
        client = get_or_create_client(tel)
        client_id = client.get("id")
        rdvs = get_rdv_client(tel, salon_id=salon.get("id"))
        _mtn = now_paris()
        _auj = _mtn.date()
        _mtn_min = _mtn.hour * 60 + _mtn.minute
        rdvs_futurs = []
        for r in rdvs:
            try:
                _rdv_date = date.fromisoformat(r.get("date", ""))
                _rdv_t = (r.get("time") or "00:00")[:5]
                _rdv_min = parse_hhmm_en_minutes(_rdv_t)
                if _rdv_date == _auj and _rdv_min < _mtn_min:
                    print(f"📋 [RDV ACTIF] ignoré — RDV passé | date={r.get('date')} heure={_rdv_t} (il est {_mtn.strftime('%H:%M')})")
                    continue
            except Exception:
                pass
            rdvs_futurs.append(r)
        rdvs = rdvs_futurs
        if not rdvs:
            return "Aucun RDV à venir pour ce client."
        rdvs_str = []
        for r in rdvs:
            print(f"📋 [RDV ACTIF] service={r.get('service', '')!r} staff_name={r.get('staff_name', '')!r} date={r.get('date', '')} time={(r.get('time') or '')[:5]}")
            rdvs_str.append(
                f"ID:{r['id']} | {r.get('date', '')} à "
                f"{(r.get('time') or '')[:5]} | {r.get('service', '')}"
            )
        update_client_context(ctx_key, client_id=client_id, en_attente_confirmation_annulation=True)
        print(f"🔒 [{nom_salon}] [VALID ANN] en_attente_confirmation_annulation=True")
        return "RDV trouvés : " + " /// ".join(rdvs_str)

    elif tool_name == "get_services":
        if prestations:
            noms = list(dict.fromkeys([
                p.get("name", "").strip()
                for p in prestations
                if p.get("name", "").strip()
            ]))
            print(f"📋 [{nom_salon}] [GET_SERVICES] {len(noms)} prestations : {noms}")
            return f"Voici toutes nos prestations ({len(noms)}) : {', '.join(noms)}."
        return "Aucune prestation enregistrée."

    elif tool_name == "get_client_info":
        client = get_or_create_client(telephone)
        client_id = client.get("id")
        client_nom = client.get("nom", "")
        update_client_context(
            ctx_key,
            prenom=client_nom.split()[0] if client_nom else None,
            client_id=client_id,
            nom=client_nom or None,
        )
        if client_nom:
            return f"Client trouvé : {client_nom}"
        return "Client nouveau ou sans nom enregistré."

    elif tool_name == "demander_rappel_conseil":
        ctx = get_client_context(ctx_key)
        prenom = (tool_input.get("nom_client")
                  or ctx.get("prenom")
                  or ctx.get("nom", "").split()[0]
                  or "Client inconnu")
        numero_client = telephone
        message_snb = (
            f"Nouveau besoin de conseil : "
            f"{prenom} ({numero_client}) "
            f"souhaite être rappelé(e) pour des conseils. "
            f"Merci de le/la contacter rapidement."
        )
        send_sms("+33782989198", message_snb)
        print(f"📱 [CONSEIL] SMS envoyé à S&B pour {prenom}")
        return (
            f"Bien sûr. Un membre de notre équipe "
            f"va vous rappeler dans les plus brefs délais "
            f"au {numero_client}. "
            f"Y a-t-il autre chose que je puisse faire pour vous ?"
        )

    elif tool_name == "rechercher_client_par_nom":
        nom = tool_input.get("nom", "").strip()
        if not nom or not supabase:
            return "Recherche impossible."
        try:
            result = supabase.table("clients").select("*").ilike("nom", f"%{nom}%").execute()
            if result.data:
                c = result.data[0]
                c_nom = c.get("nom", "")
                update_client_context(
                    ctx_key,
                    prenom=c_nom.split()[0] if c_nom else None,
                    client_id=c.get("id"),
                    nom=c_nom or None,
                )
                return f"Client trouvé par nom : {c_nom} — tél : {c.get('telephone', 'inconnu')}"
            return f"Aucun client nommé '{nom}' trouvé."
        except Exception as e:
            return f"Erreur recherche client : {e}"

    elif tool_name == "verifier_coiffeur_disponible":
        jour = corriger_annee_date(tool_input.get("jour"))
        heure = tool_input.get("heure")
        coiffeur_souhaite = tool_input.get("coiffeur_souhaite")
        if coiffeur_souhaite:
            update_client_context(ctx_key, rdv_coiffeur=coiffeur_souhaite)
        disponibles = get_coiffeurs_disponibles(jour, heure, coiffeurs=coiffeurs)
        if coiffeur_souhaite:
            _cs_norm = coiffeur_souhaite.strip().lower()
            coiffeur_libre = any(c["nom"].strip().lower() == _cs_norm for c in disponibles)
            if coiffeur_libre:
                return f"{coiffeur_souhaite} est disponible à {heure}."
            creneaux_coiffeur = []
            heure_test = heure
            for _ in range(8):
                heure_test = ajouter_minutes_hhmm(heure_test, 30)
                dispo = get_coiffeurs_disponibles(jour, heure_test, coiffeurs=coiffeurs)
                if any(c["nom"].strip().lower() == _cs_norm for c in dispo):
                    creneaux_coiffeur.append(heure_test)
                if len(creneaux_coiffeur) >= 2:
                    break
            noms_dispo = [c["nom"] for c in disponibles]
            return (f"{coiffeur_souhaite} est pris à {heure}. "
                    f"Disponible : {', '.join(creneaux_coiffeur) or 'plus tard'}. "
                    f"Autres coiffeurs libres : {', '.join(noms_dispo) or 'aucun'}.")
        noms = [c["nom"] for c in disponibles]
        return f"Coiffeurs disponibles à {heure} : {', '.join(noms) or 'aucun'}."

    elif tool_name == "proposer_creneaux":
        jour = tool_input.get("jour")
        heure_souhaitee = tool_input.get("heure_souhaitee") or salon.get("horaire_ouverture", "09:00")
        update_client_context(ctx_key, rdv_en_cours=True)
        _coiffeur_prop = tool_input.get("coiffeur") or get_client_context(ctx_key).get("rdv_coiffeur", "") or None
        creneaux = get_prochains_creneaux_disponibles(jour, heure_souhaitee, coiffeur=_coiffeur_prop,
                                                      coiffeurs=coiffeurs, salon=salon)
        if creneaux:
            _coiffeur_label = f" avec {_coiffeur_prop}" if _coiffeur_prop else ""
            return f"Créneaux disponibles le {jour}{_coiffeur_label} : {', '.join(creneaux)}."
        return f"Aucun créneau disponible le {jour}."

    elif tool_name == "ajouter_liste_attente":
        client = get_or_create_client(telephone)
        client_id = client.get("id")
        client_nom_tool = tool_input.get("client_nom") or client.get("nom") or telephone
        if supabase:
            try:
                supabase.table("liste_attente").insert({
                    "client_id": client_id,
                    "telephone": telephone,
                    "nom": client_nom_tool,
                    "jour_souhaite": tool_input.get("jour_souhaite"),
                    "prestation": tool_input.get("prestation"),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                return f"Erreur liste d'attente : {e}"
        return f"Client ajouté en liste d'attente pour le {tool_input.get('jour_souhaite')}."

    elif tool_name == "transfert_humain":
        raison = tool_input.get("raison", "demande du client")
        ctx = get_client_context(ctx_key)
        nom = ctx.get("prenom") or ctx.get("nom") or telephone
        if SMS_ENABLED and twilio_client:
            try:
                twilio_client.messages.create(
                    to=tel_salon or twilio_num,
                    from_=twilio_num,
                    body=f"⚠️ Transfert demandé par {nom} ({telephone}). Raison : {raison}. Rappeler immédiatement.",
                )
            except Exception as e:
                print(f"SMS transfert erreur : {e}")
        else:
            logging.info(f"[SMS DÉSACTIVÉ] SMS transfert non envoyé à {tel_salon or twilio_num}")
        return f"Transfert initié. SMS envoyé au salon pour rappeler {nom}."

    return "Fonction inconnue."

# ====================================================
# HELPERS AVANT APPEL GPT
# ====================================================
def shampoing_deja_demande(telephone: str) -> bool:
    """Retourne True si le shampoing a déjà été mentionné dans l'historique."""
    for msg in get_conversation_history(telephone):
        if "shampoing" in str(msg.get("content", "")).lower():
            return True
    return False

def get_reponse_cache(message: str, salon: dict = None) -> str | None:
    """Retourne une réponse immédiate pour les questions fréquentes sans appel GPT."""
    ml = message.lower().strip()
    if any(m in ml for m in ["horaire", "ouvert", "fermé", "quand", "jusqu'à", "à partir"]) \
       or ("heure" in ml and "rendez" not in ml):
        s = salon or {}
        jours = s.get("jours_ouverts") or ["mardi","mercredi","jeudi","vendredi","samedi"]
        jours_str = ', '.join([j.capitalize() for j in jours])
        ouv = s.get("horaire_ouverture", "09:00")
        fer = s.get("horaire_fermeture", "18:00")
        return f"On est ouvert {jours_str} de {ouv} à {fer}."
    if any(m in ml for m in ["adresse", "situé", "trouver", "localisation", "comment venir"]) \
       or ("où" in ml and len(ml) < 40):
        adresse = (salon or {}).get("adresse", "")
        if adresse:
            return f"On est situé au {adresse}."
    return None

def detecter_humeur(message: str) -> str:
    """Détecte l'humeur du client pour adapter le ton de l'agent."""
    ml = message.lower()
    if any(m in ml for m in ["vite", "rapidement", "urgent", "pressé", "pas le temps"]):
        return "pressé"
    if any(m in ml for m in ["problème", "soucis", "compliqué", "difficile", "impossible"]):
        return "stressé"
    if any(m in ml for m in ["super", "génial", "parfait", "excellent", "top", "cool"]):
        return "joyeux"
    return "neutre"

# ====================================================
# AGENT PRINCIPAL AVEC GPT-4o OPTIMISÉ
# ====================================================
def appeler_verifier_disponibilite(prestation: str, jour: str, heure: str, telephone: str,
                                    coiffeur: str = "", ctx_key: str = None,
                                    salon: dict = None, coiffeurs: list = None, prestations: list = None) -> str:
    """C5 — Appel direct à verifier_disponibilite sans passer par GPT (fallback)."""
    print(f"✅ [DISPO APPELÉE] direct Python | prestation={prestation!r} | jour={jour!r} | heure={heure!r} | coiffeur={coiffeur!r}")
    return process_tool_call(
        "verifier_disponibilite",
        {"prestation": prestation, "jour": jour, "heure": heure, "coiffeur": coiffeur or ""},
        telephone,
        ctx_key=ctx_key or telephone,
        salon=salon or {},
        coiffeurs=coiffeurs or [],
        prestations=prestations or [],
    )


def run_agent(message_user: str, telephone: str,
              ctx_key: str = None, salon: dict = None,
              coiffeurs: list = None, prestations: list = None) -> str:
    """
    Exécute l'agent GPT-4o avec function calling (multi-salon).
    salon, coiffeurs, prestations sont chargés dynamiquement par /appel avant l'appel.
    """
    global session_tokens_input, session_tokens_output, session_tokens_total
    global session_nb_echanges, session_cout_usd, session_cout_eur

    ctx_key    = ctx_key    or telephone
    salon      = salon      or {}
    coiffeurs  = coiffeurs  or []
    prestations = prestations or []
    nom_salon  = salon.get("nom", "")
    tel_salon  = salon.get("telephone", "")
    twilio_num = salon.get("twilio_number", "")

    if not client_openai:
        return "⚠️ Erreur: API OpenAI non configurée. Vérifiez votre clé API."

    # Cache réponses fréquentes (évite un appel GPT)
    reponse_cache = get_reponse_cache(message_user, salon=salon)
    if reponse_cache:
        add_to_history(ctx_key, "assistant", reponse_cache)
        return reponse_cache

    # Ajouter le message utilisateur à l'historique
    add_to_history(ctx_key, "user", message_user)

    # ── C4 — Scan historique complet pour reconstruire le contexte RDV ──────────
    _ctx_scan = get_client_context(ctx_key)
    _hist_full = get_conversation_history(ctx_key)
    _all_user_text = " ".join(
        m.get("content", "") for m in _hist_full if m.get("role") == "user"
    ).lower()

    # Prestation depuis historique
    if not _ctx_scan.get("rdv_prestation") and prestations:
        for _p_scan in prestations:
            _nom_scan = (_p_scan.get("name") or "").lower().strip()
            if _nom_scan and _nom_scan in _all_user_text:
                update_client_context(ctx_key, rdv_prestation=_p_scan["name"])
                break

    # Heure depuis historique
    if not _ctx_scan.get("rdv_heure"):
        _m_h_scan = re.search(r'\b(\d{1,2})h(\d{2})?\b|\b(\d{1,2}):(\d{2})\b', _all_user_text)
        if _m_h_scan:
            if _m_h_scan.group(1) is not None:
                _hh_s, _mm_s = int(_m_h_scan.group(1)), _m_h_scan.group(2) or "00"
            else:
                _hh_s, _mm_s = int(_m_h_scan.group(3)), _m_h_scan.group(4)
            update_client_context(ctx_key, rdv_heure=f"{_hh_s:02d}:{_mm_s}")

    # Jour depuis historique
    if not _ctx_scan.get("rdv_jour"):
        _today_scan = now_paris().date()
        _noms_j_scan = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        if "demain" in _all_user_text:
            update_client_context(ctx_key, rdv_jour=(_today_scan + timedelta(days=1)).isoformat())
        elif "aujourd" in _all_user_text:
            update_client_context(ctx_key, rdv_jour=_today_scan.isoformat())
        else:
            for _idx_scan, _nom_j_scan in enumerate(_noms_j_scan):
                if _nom_j_scan in _all_user_text:
                    _delta_scan = (_idx_scan - _today_scan.weekday()) % 7 or 7
                    update_client_context(ctx_key, rdv_jour=(_today_scan + timedelta(days=_delta_scan)).isoformat())
                    break

    # Coiffeur depuis historique
    if not _ctx_scan.get("rdv_coiffeur") and coiffeurs:
        for _c_scan in coiffeurs:
            _nc = (_c_scan.get("nom") or "").lower()
            if _nc and _nc in _all_user_text:
                update_client_context(ctx_key, rdv_coiffeur=_c_scan["nom"])
                break

    _ctx_after_scan = get_client_context(ctx_key)
    print(f"📋 [{nom_salon}] [SCAN HISTORIQUE] prestation={_ctx_after_scan.get('rdv_prestation') or '—'} | jour={_ctx_after_scan.get('rdv_jour') or '—'} | heure={_ctx_after_scan.get('rdv_heure') or '—'} | coiffeur={_ctx_after_scan.get('rdv_coiffeur') or '—'}")

    # Détection humeur
    humeur = detecter_humeur(message_user)
    update_client_context(ctx_key, humeur=humeur)

    # Détection langue anglaise
    mots_anglais = ["hello", "hi", "appointment", "booking", "please", "thank", "yes", "no", "hair", "cut"]
    est_anglais = any(mot in message_user.lower() for mot in mots_anglais)

    # Détecter réponse au shampoing
    message_lower_shamp = message_user.lower()
    ctx_shamp = get_client_context(ctx_key)
    if not ctx_shamp.get("shampoing_repondu"):
        history_shamp = get_conversation_history(ctx_key)
        for msg in reversed(history_shamp[:-1]):
            if msg.get("role") == "assistant":
                if "shampoing" in str(msg.get("content", "")).lower():
                    avec = any(m in message_lower_shamp for m in ["oui", "avec", "s'il vous plaît", "volontiers"])
                    update_client_context(ctx_key, shampoing_repondu=True, avec_shampoing=avec)
                break

    # Détecter prénom dans un message court
    ctx = get_client_context(ctx_key)
    if not ctx.get("prenom") and 1 <= len(message_user.strip().split()) <= 3:
        prenom_candidat = message_user.strip().split()[0].capitalize()
        if prenom_candidat.isalpha():
            update_client_context(ctx_key, prenom=prenom_candidat)

    # ── Extraction immédiate des infos RDV depuis le message client ──
    _msg_rdv_lower = message_user.lower()
    _ctx_rdv_pre = get_client_context(ctx_key)

    # Prestation
    if not _ctx_rdv_pre.get("rdv_prestation") and prestations:
        for _p_rdv in prestations:
            _nom_p_rdv = (_p_rdv.get("name") or "").lower().strip()
            if _nom_p_rdv and _nom_p_rdv in _msg_rdv_lower:
                update_client_context(ctx_key, rdv_prestation=_p_rdv["name"])
                print(f"📋 [{nom_salon}] [CONTEXTE RDV] prestation extraite du message : {_p_rdv['name']!r}")
                break

    # Heure : "14h", "14h30" → HH:MM
    if not _ctx_rdv_pre.get("rdv_heure"):
        _m_h = re.search(r'\b(\d{1,2})h(\d{2})?\b', _msg_rdv_lower)
        if _m_h:
            _hh = int(_m_h.group(1))
            _mm = _m_h.group(2) or "00"
            _heure_extracted = f"{_hh:02d}:{_mm}"
            update_client_context(ctx_key, rdv_heure=_heure_extracted)
            print(f"📋 [{nom_salon}] [CONTEXTE RDV] heure extraite du message : {_heure_extracted!r}")

    # Jour : noms de jours ou "demain" → prochaine date ISO (Europe/Paris)
    if not _ctx_rdv_pre.get("rdv_jour"):
        _today_rdv = now_paris().date()
        _noms_j_extr = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        if "demain" in _msg_rdv_lower:
            _jour_iso = (_today_rdv + timedelta(days=1)).isoformat()
            update_client_context(ctx_key, rdv_jour=_jour_iso)
            print(f"📋 [{nom_salon}] [CONTEXTE RDV] jour extrait : demain → {_jour_iso}")
        elif "aujourd" in _msg_rdv_lower:
            update_client_context(ctx_key, rdv_jour=_today_rdv.isoformat())
            print(f"📋 [{nom_salon}] [CONTEXTE RDV] jour extrait : aujourd'hui → {_today_rdv.isoformat()}")
        else:
            for _idx_j, _nom_j in enumerate(_noms_j_extr):
                if _nom_j in _msg_rdv_lower:
                    _delta_j = (_idx_j - _today_rdv.weekday()) % 7
                    if _delta_j == 0:
                        _delta_j = 7
                    _jour_iso = (_today_rdv + timedelta(days=_delta_j)).isoformat()
                    update_client_context(ctx_key, rdv_jour=_jour_iso)
                    print(f"📋 [{nom_salon}] [CONTEXTE RDV] jour extrait : {_nom_j} → {_jour_iso}")
                    break

    # Coiffeur — insensible aux accents, essaie nom complet puis chaque partie
    _msg_norm_coif = normaliser_texte(_msg_rdv_lower)
    if not _ctx_rdv_pre.get("rdv_coiffeur") and coiffeurs:
        for _c_rdv in coiffeurs:
            _nom_c_norm = normaliser_texte(_c_rdv.get("nom") or "")
            if not _nom_c_norm:
                continue
            if _nom_c_norm in _msg_norm_coif:
                update_client_context(ctx_key, rdv_coiffeur=_c_rdv["nom"])
                print(f"📋 [{nom_salon}] [CONTEXTE RDV] coiffeur extrait : {_c_rdv['nom']!r}")
                break
            _parties_nom = [p for p in _nom_c_norm.split() if len(p) >= 3]
            if any(p in _msg_norm_coif for p in _parties_nom):
                update_client_context(ctx_key, rdv_coiffeur=_c_rdv["nom"])
                print(f"📋 [{nom_salon}] [CONTEXTE RDV] coiffeur extrait (partiel) : {_c_rdv['nom']!r}")
                break

    # ── VALIDATION 1c — Coiffeur extrait ce tour + prestation dans le message : vérifier compétence ──
    _coif_juste_1c = (
        not _ctx_rdv_pre.get("rdv_coiffeur")
        and get_client_context(ctx_key).get("rdv_coiffeur", "")
    )
    if _coif_juste_1c and not get_client_context(ctx_key).get("rdv_prestation", "") and prestations:
        print(f"🔍 [{nom_salon}] [VALID 1c] coiffeur {_coif_juste_1c!r} détecté dans speech — scan prestation")
        _prest_msg_1c = None
        _msg_norm_1c = normaliser_texte(_msg_rdv_lower)
        for _p_1c in prestations:
            _p_nom_1c = normaliser_texte(_p_1c.get("name") or "")
            if not _p_nom_1c:
                continue
            if _p_nom_1c in _msg_norm_1c:
                _prest_msg_1c = _p_1c.get("name")
                break
            for _word_1c in _msg_norm_1c.split():
                if len(_word_1c) >= 4 and _word_1c in _p_nom_1c:
                    _prest_msg_1c = _p_1c.get("name")
                    break
            if _prest_msg_1c:
                break
        if _prest_msg_1c and coiffeurs:
            _comp_1c = coiffeurs_competents(_prest_msg_1c, coiffeurs=coiffeurs)
            _coif_nom_1c = _coif_juste_1c
            _coif_norm_1c = normaliser_texte(_coif_nom_1c)
            _est_comp_1c = any(
                normaliser_texte(c["nom"]) == _coif_norm_1c
                or _coif_norm_1c in normaliser_texte(c["nom"])
                or normaliser_texte(c["nom"]) in _coif_norm_1c
                for c in _comp_1c
            )
            if not _est_comp_1c:
                _alt_1c = [c["nom"] for c in _comp_1c]
                _alt_str_1c = " et ".join(_alt_1c) if _alt_1c else "nos autres coiffeurs"
                _resp_1c = (
                    f"{_coif_nom_1c} ne fait pas {_prest_msg_1c}. "
                    f"Cette prestation est réalisée par {_alt_str_1c}."
                )
                add_to_history(ctx_key, "assistant", _resp_1c)
                print(f"❌ [{nom_salon}] [VALID 1c] {_coif_nom_1c!r} non compétent pour {_prest_msg_1c!r} → réponse directe sans GPT")
                return _resp_1c
            else:
                update_client_context(ctx_key, rdv_prestation=_prest_msg_1c)
                print(f"✅ [{nom_salon}] [VALID 1c] {_coif_nom_1c!r} compétent pour {_prest_msg_1c!r} → prestation stockée")

    # Construire le system prompt
    sys_prompt = build_system_prompt(ctx_key, telephone, salon, coiffeurs, prestations)
    if est_anglais:
        sys_prompt += "\nLe client parle anglais. Réponds en anglais mais garde les données en français dans Supabase."

    # Limiter l'historique à 8 messages pour performance
    history = get_conversation_history(ctx_key)
    if len(history) > 8:
        history = history[-8:]
        conversation_history[ctx_key] = history

    # Préparer les messages avec le system prompt en premier
    messages = [{"role": "system", "content": sys_prompt}] + get_conversation_history(ctx_key)

    messages = clean_messages(messages)

    # ── C2 — Forcer verifier_disponibilite spécifiquement quand contexte complet ──
    _hist_text = " ".join(
        str(m.get("content", "")) for m in get_conversation_history(ctx_key)
    ).lower()
    _dispos_deja_verif = "disponibilit" in _hist_text or "libre" in _hist_text or "occupé" in _hist_text
    _ctx_force = get_client_context(ctx_key)
    _rdv_p  = _ctx_force.get("rdv_prestation", "")
    _rdv_j  = _ctx_force.get("rdv_jour", "")
    _rdv_h  = _ctx_force.get("rdv_heure", "")
    _ctx_has_all = bool(_rdv_p and _rdv_j and _rdv_h)

    # Fallback : détection dans le texte de l'historique
    _jour_detecte  = any(j in _hist_text for j in
                         ["lundi", "mardi", "mercredi", "jeudi", "vendredi",
                          "samedi", "dimanche", "demain", "aujourd", "prochain"])
    _heure_detectee = bool(re.search(r'\b\d{1,2}h\d{0,2}\b|\d{1,2}:\d{2}', _hist_text))

    # ── C2-ANNULATION — Forcer get_rdv_client_actif quand client parle d'annulation ──
    _mots_annulation = ["annul", "supprim", "cancel", "enlev", "effac", "retir", "mon rendez-vous"]
    _ctx_annulation = any(m in message_user.lower() for m in _mots_annulation)
    _annulation_detectee = _ctx_annulation or _ctx_force.get("en_attente_confirmation_annulation", False)
    _rdv_deja_recupere = "rdv trouvé" in _hist_text or "aucun rdv" in _hist_text or "get_rdv_client_actif" in _hist_text

    if _ctx_annulation and not _rdv_deja_recupere:
        _tool_choice = {"type": "function", "function": {"name": "get_rdv_client_actif"}}
        print(f"🔧 [FORCE TOOL] Annulation détectée → tool_choice=get_rdv_client_actif")
    elif _annulation_detectee:
        _tool_choice = "auto"
        print(f"🔧 [FORCE TOOL] Annulation/confirmation en cours → tool_choice=auto (skip FORCE TOOL)")
    elif _ctx_has_all and not _dispos_deja_verif:
        # Vérifier repos AVANT de forcer verifier_disponibilite
        if _rdv_p and coiffeurs:
            _NOMS_J_FT = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
            _comp_ft = coiffeurs_competents(_rdv_p, coiffeurs=coiffeurs)
            if not _comp_ft:
                _prest_list_ft = ", ".join([p.get("name", "") for p in prestations if p.get("name")]) or "nos prestations"
                _resp_ft_a = (f"Je suis désolé, aucun de nos coiffeurs ne propose \"{_rdv_p}\". "
                              f"Voici nos prestations : {_prest_list_ft}. Laquelle vous intéresse ?")
                add_to_history(ctx_key, "assistant", _resp_ft_a)
                return _resp_ft_a
            _nom_jour_ft = ""
            _comp_ft_dispo = list(_comp_ft)
            try:
                _nom_jour_ft = _NOMS_J_FT[date.fromisoformat(_rdv_j).weekday()]
                _comp_ft_dispo = [
                    c for c in _comp_ft
                    if _nom_jour_ft not in [r.lower() for r in (c.get("jours_repos") or [])]
                ]
            except Exception:
                pass
            if _nom_jour_ft and not _comp_ft_dispo:
                _coif_ft = _comp_ft[0]
                _jours_ouv_ft = [j.lower() for j in (salon.get("jours_ouverts") or [])]
                _next_travail_ft = None
                try:
                    _d_rdv_ft = date.fromisoformat(_rdv_j)
                    for _di_ft in range(1, 8):
                        _d_next_ft = _d_rdv_ft + timedelta(days=_di_ft)
                        _nom_next_ft = _NOMS_J_FT[_d_next_ft.weekday()]
                        _repos_next_ft = [r.lower() for r in (_coif_ft.get("jours_repos") or [])]
                        _est_ouv_ft = not _jours_ouv_ft or _nom_next_ft in _jours_ouv_ft
                        if _nom_next_ft not in _repos_next_ft and _est_ouv_ft:
                            _next_travail_ft = _nom_next_ft
                            break
                except Exception:
                    pass
                _resp_ft_b = (
                    f"{_coif_ft['nom']} fait les {_rdv_p} mais ne travaille pas le {_nom_jour_ft}. "
                    + (f"Il reprend le {_next_travail_ft}." if _next_travail_ft
                       else "Souhaitez-vous choisir un autre jour ?")
                )
                add_to_history(ctx_key, "assistant", _resp_ft_b)
                print(f"⚠️ [{nom_salon}] [FORCE TOOL] {_coif_ft['nom']!r} compétent mais en repos {_nom_jour_ft} → reprend {_next_travail_ft or '?'} → TwiML direct")
                return _resp_ft_b
        # Forcer SPÉCIFIQUEMENT verifier_disponibilite — GPT ne peut PAS répondre en texte
        _tool_choice = {"type": "function", "function": {"name": "verifier_disponibilite"}}
        print(f"🔧 [FORCE TOOL] prestation={_rdv_p!r} jour={_rdv_j!r} heure={_rdv_h!r} → tool_choice=verifier_disponibilite")
    elif _rdv_p and not _rdv_j:
        _tool_choice = "auto"
        print(f"🔧 [FORCE TOOL] prestation={_rdv_p!r} jour=— heure=— → tool_choice=auto (demander le jour)")
    elif _rdv_p and _rdv_j and not _rdv_h:
        _tool_choice = "auto"
        print(f"🔧 [FORCE TOOL] prestation={_rdv_p!r} jour={_rdv_j!r} heure=— → tool_choice=auto (demander l'heure)")
    elif (_jour_detecte and _heure_detectee) and not _dispos_deja_verif:
        _tool_choice = "required"
        print(f"🔧 [FORCE TOOL] jour+heure détectés dans historique → tool_choice=required")
    else:
        _tool_choice = "auto"

    # ── VALIDATION 1 — Prestation sans coiffeur : vérifier compétences ────────
    _NOMS_J_V1 = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    _rdv_coiffeur_v = _ctx_force.get("rdv_coiffeur", "")
    if _rdv_p and not _rdv_coiffeur_v and coiffeurs:
        # Étape 1 — compétents sans filtre repos
        _comp_v1 = coiffeurs_competents(_rdv_p, coiffeurs=coiffeurs)
        print(f"🔍 [{nom_salon}] [VALID 1] prestation={_rdv_p!r} | compétents (tous) : {[c['nom'] for c in _comp_v1]}")

        # CAS A — vraiment aucun coiffeur ne fait cette prestation
        if not _comp_v1:
            _prest_list_v1 = ", ".join([p.get("name", "") for p in prestations if p.get("name")]) or "nos prestations"
            _resp_v1 = (f"Je suis désolé, aucun de nos coiffeurs ne propose la prestation \"{_rdv_p}\". "
                        f"Voici nos prestations disponibles : {_prest_list_v1}. Laquelle vous intéresse ?")
            add_to_history(ctx_key, "assistant", _resp_v1)
            return _resp_v1

        # Étape 2 — filtrer par disponibilité ce jour (repos)
        _nom_jour_v1 = ""
        _comp_v1_dispo = list(_comp_v1)
        if _rdv_j:
            try:
                _nom_jour_v1 = _NOMS_J_V1[date.fromisoformat(_rdv_j).weekday()]
                _comp_v1_dispo = [
                    c for c in _comp_v1
                    if _nom_jour_v1 not in [r.lower() for r in (c.get("jours_repos") or [])]
                ]
            except Exception:
                pass

        # CAS B — compétents existent mais tous en repos ce jour
        if _nom_jour_v1 and not _comp_v1_dispo:
            _coif_b = _comp_v1[0]
            _jours_ouv_v1 = [j.lower() for j in (salon.get("jours_ouverts") or [])]
            _next_travail_v1 = None
            try:
                _d_rdv_v1 = date.fromisoformat(_rdv_j)
                for _di_v1 in range(1, 8):
                    _d_next_v1 = _d_rdv_v1 + timedelta(days=_di_v1)
                    _nom_next_v1 = _NOMS_J_V1[_d_next_v1.weekday()]
                    _repos_next_v1 = [r.lower() for r in (_coif_b.get("jours_repos") or [])]
                    _est_ouv_v1 = not _jours_ouv_v1 or _nom_next_v1 in _jours_ouv_v1
                    if _nom_next_v1 not in _repos_next_v1 and _est_ouv_v1:
                        _next_travail_v1 = _nom_next_v1
                        break
            except Exception:
                pass
            _resp_v1_repos = (
                f"{_coif_b['nom']} fait les {_rdv_p} mais ne travaille pas le {_nom_jour_v1}. "
                + (f"Il reprend le {_next_travail_v1}." if _next_travail_v1
                   else "Souhaitez-vous choisir un autre jour ?")
            )
            add_to_history(ctx_key, "assistant", _resp_v1_repos)
            print(f"⚠️ [{nom_salon}] [VALID 1] {_coif_b['nom']!r} compétent mais en repos {_nom_jour_v1} → reprend {_next_travail_v1 or '?'}")
            return _resp_v1_repos

        # Étape 3 — auto-assigner si un seul compétent disponible
        if len(_comp_v1_dispo) == 1:
            _rdv_coiffeur_v = _comp_v1_dispo[0]["nom"]
            update_client_context(ctx_key, rdv_coiffeur=_rdv_coiffeur_v)
            print(f"✅ [{nom_salon}] [VALID 1] Un seul coiffeur compétent dispo → assigné : {_rdv_coiffeur_v!r}")

    # ── VALIDATION 1b — Coiffeur déjà en contexte : vérifier ses compétences ─
    elif _rdv_p and _rdv_coiffeur_v and coiffeurs:
        _comp_v1b = coiffeurs_competents(_rdv_p, coiffeurs=coiffeurs)
        _coif_norm_v1b = _rdv_coiffeur_v.strip().lower()
        _est_competent_v1b = any(
            c["nom"].strip().lower() == _coif_norm_v1b
            or _coif_norm_v1b in c["nom"].strip().lower()
            or c["nom"].strip().lower() in _coif_norm_v1b
            for c in _comp_v1b
        )
        if not _est_competent_v1b:
            _alt_names_v1b = [c["nom"] for c in _comp_v1b]
            _alt_str_v1b = " et ".join(_alt_names_v1b) if _alt_names_v1b else "nos autres coiffeurs"
            _resp_v1b = (
                f"{_rdv_coiffeur_v} ne fait pas {_rdv_p}. "
                f"Cette prestation est réalisée par {_alt_str_v1b}."
            )
            add_to_history(ctx_key, "assistant", _resp_v1b)
            print(f"⚠️ [{nom_salon}] [VALID 1b] {_rdv_coiffeur_v!r} incompétent pour {_rdv_p!r} → retour immédiat")
            return _resp_v1b

    # ── VALIDATION 2 — Jour + coiffeur : vérifier jour de repos ──────────────
    _rdv_coiffeur_v = get_client_context(ctx_key).get("rdv_coiffeur", "") or _rdv_coiffeur_v
    if _rdv_j and _rdv_coiffeur_v:
        _noms_jours_v = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        try:
            _nom_jour_v = _noms_jours_v[date.fromisoformat(_rdv_j).weekday()]
        except Exception:
            _nom_jour_v = ""
        if _nom_jour_v:
            _coif_norm_v2 = _rdv_coiffeur_v.strip().lower()
            # Matching tolérant : exact, ou nom en context dans nom complet, ou inverse
            _coif_obj_v = next(
                (c for c in coiffeurs if
                 c["nom"].strip().lower() == _coif_norm_v2
                 or _coif_norm_v2 in c["nom"].strip().lower()
                 or c["nom"].strip().lower() in _coif_norm_v2),
                None
            )
            if _coif_obj_v:
                # Lire days_off depuis le dict coiffeur (champ jours_repos normalisé par get_coiffeurs)
                _repos_v = [r.lower() for r in (_coif_obj_v.get("jours_repos") or [])]
                if _nom_jour_v in _repos_v:
                    _coifs_alt_v = [c["nom"] for c in coiffeurs
                                    if c["nom"].strip().lower() != _coif_obj_v["nom"].strip().lower()
                                    and _nom_jour_v not in [r.lower() for r in (c.get("jours_repos") or [])]]
                    # Calculer le prochain jour de travail du coiffeur
                    _next_travail_v = None
                    try:
                        _noms_j_7_v = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
                        _jours_ouv_v = [j.lower() for j in (salon.get("jours_ouverts") or [])]
                        _d_rdv_v = date.fromisoformat(_rdv_j)
                        for _di_v in range(1, 8):
                            _d_next_v = _d_rdv_v + timedelta(days=_di_v)
                            _nom_next_v = _noms_j_7_v[_d_next_v.weekday()]
                            _repos_next_v = [r.lower() for r in (_coif_obj_v.get("jours_repos") or [])]
                            _est_ouv_v = not _jours_ouv_v or _nom_next_v in _jours_ouv_v
                            if _nom_next_v not in _repos_next_v and _est_ouv_v:
                                _next_travail_v = _nom_next_v
                                break
                    except Exception:
                        pass
                    if _coifs_alt_v:
                        _inject_v2 = (
                            f"VALIDATION : {_coif_obj_v['nom']} ne travaille pas le {_nom_jour_v} (repos). "
                            + (f"Il reprend le {_next_travail_v}. " if _next_travail_v else "")
                            + f"Dis exactement ça au client, rien d'autre. Ne liste pas les prestations."
                        )
                    else:
                        _inject_v2 = (
                            f"VALIDATION : Aucun coiffeur disponible le {_nom_jour_v}. "
                            f"{_coif_obj_v['nom']} est en repos ce jour. "
                            + (f"Il reprend le {_next_travail_v}. " if _next_travail_v else "")
                            + f"Propose le prochain jour où il travaille."
                        )
                    messages.append({"role": "user", "content": _inject_v2})
                    print(f"⚠️ [VALID 2] {_coif_obj_v['nom']} en repos {_nom_jour_v} → reprend {_next_travail_v or '?'}")

    # ── C7 — Log contexte RDV avant GPT ─────────────────────────────────────
    print(f"📋 [CONTEXTE RDV AVANT GPT] prestation={_rdv_p or '—'} | jour={_rdv_j or '—'} | heure={_rdv_h or '—'} | coiffeur={_rdv_coiffeur_v or '—'}")

    # Appeler GPT-4o avec function calling
    if not TOOLS:
        print("❌ [ERROR] tools vide — TOOLS non chargé, function calling désactivé")
    print(f"🔧 [GPT INPUT] tool_choice={_tool_choice} | messages_count={len(messages)}")
    try:
        response = client_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=TOOLS,
            tool_choice=_tool_choice,
            temperature=0.1,
            max_tokens=100,
            presence_penalty=0.0,
            frequency_penalty=0.0,
            stream=False,
        )
        # ÉTAPE 2 : Récupérer et accumuler les tokens
        session_tokens_input += response.usage.prompt_tokens
        session_tokens_output += response.usage.completion_tokens
        session_tokens_total += response.usage.total_tokens
        session_nb_echanges += 1

    except Exception as e:
        print(f"Erreur GPT-4o: {e}")
        # Nettoyer l'historique en cas d'erreur
        history = get_conversation_history(telephone)
        if history and history[-1].get('role') == 'assistant' \
           and history[-1].get('tool_calls'):
            history.pop()
            print("⚠️ [CLEAN] Dernier tool_call retiré après erreur")
        return "Désolé, pouvez-vous répéter ?"

    # Traiter la réponse
    choice = response.choices[0]
    # ── C7 — Log output GPT ──────────────────────────────────────────────────
    _gpt_out_type = "tool_call" if choice.message.tool_calls else "text"
    _gpt_out_content = str(choice.message.content or "")[:50] if not choice.message.tool_calls else choice.message.tool_calls[0].function.name
    print(f"🔧 [GPT OUTPUT] type={_gpt_out_type} | content={_gpt_out_content!r}")

    # Boucle tool calls — max 3 itérations (gère les chaînes de tools)
    import random as _random
    OUTILS_LENTS = {
        "verifier_coiffeur_disponible", "proposer_creneaux",
        "prendre_rdv", "get_rdv_client_actif", "verifier_disponibilite",
    }
    MSGS_ATTENTE = [
        "Un instant, je regarde les disponibilités.",
        "Laissez-moi vérifier ça pour vous.",
        "Je jette un œil au planning.",
        "Une seconde, je consulte l'agenda.",
        "Voyons voir ce qu'on a de disponible.",
    ]
    for _tool_iteration in range(1):  # max 1 tool call par tour — GPT doit répondre au client après
        if not choice.message.tool_calls:
            break

        # BUG3 : si GPT a généré du texte ET un tool_call, ignorer le texte
        if choice.message.content:
            print(f"⚠️ [BUG3] GPT a généré texte ET tool_call — texte ignoré: {choice.message.content[:60]!r}")

        # Message d'attente uniquement sur la première itération
        if _tool_iteration == 0:
            outil_utilise = choice.message.tool_calls[0].function.name
            if outil_utilise in OUTILS_LENTS:
                update_client_context(ctx_key, message_attente=_random.choice(MSGS_ATTENTE))

        tool_calls_data = []
        for tc in choice.message.tool_calls:
            tool_calls_data.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments
                }
            })
        add_assistant_message_with_tools(ctx_key, content=None, tool_calls=tool_calls_data)

        for tool_call in choice.message.tool_calls:
            tool_name = tool_call.function.name
            tool_input = json.loads(tool_call.function.arguments)
            print(f"🔧 [{nom_salon}] [TOOL] itération={_tool_iteration+1} {tool_name} | args={tool_input}")
            tool_result = process_tool_call(tool_name, tool_input, telephone,
                                            ctx_key=ctx_key, salon=salon,
                                            coiffeurs=coiffeurs, prestations=prestations)
            print(f"✅ [{nom_salon}] [TOOL] {tool_name} → {str(tool_result)[:120]}")
            add_tool_result(ctx_key, tool_call.id, tool_result)

        # Relancer GPT avec le résultat des tools
        messages = [{"role": "system", "content": sys_prompt}] + get_conversation_history(ctx_key)
        messages = clean_messages(messages)

        if not TOOLS:
            print("❌ [ERROR] tools vide — TOOLS non chargé sur l'appel GPT post-tool")
        try:
            response = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=TOOLS,
                tool_choice="none",  # forcer réponse vocale — pas de chaîne de tools
                temperature=0.1,
                max_tokens=100,
                presence_penalty=0.0,
                frequency_penalty=0.0,
                stream=False,
            )
            session_tokens_input += response.usage.prompt_tokens
            session_tokens_output += response.usage.completion_tokens
            session_tokens_total += response.usage.total_tokens
            session_nb_echanges += 1

        except Exception as e:
            print(f"Erreur GPT-4o (post-tool itération {_tool_iteration+1}): {e}")
            history = get_conversation_history(ctx_key)
            if history and history[-1].get('role') == 'assistant' \
               and history[-1].get('tool_calls'):
                history.pop()
                print("⚠️ [CLEAN] Dernier tool_call retiré après erreur post-tool")
            return "Désolé, pouvez-vous répéter ?"

        choice = response.choices[0]
        print(f"🔍 [APRES-TOOL] Itération {_tool_iteration+1} — réponse GPT: {str(choice.message.content or '[tool_call]')[:100]}")

    # Extraire la réponse texte
    response_text = choice.message.content

    # ── C1 + C3 — Interception "je vais vérifier" / "je vais récupérer" et réponse vide ──
    PHRASES_ATTENTE_INTERDITES = [
        "je vais vérifier", "je vérifie", "je vais chercher", "je vais regarder",
        "un instant", "laissez-moi", "je consulte", "je regarde", "permettez-moi",
        "je recherche", "je vais contrôler", "je vais consulter",
        "je vais récupérer", "je récupère", "je vais chercher vos",
        "je vais consulter vos", "je vais regarder vos", "je recherche vos",
        "laissez-moi chercher",
    ]
    _ctx_intercept = get_client_context(ctx_key)
    _rdv_pi = _ctx_intercept.get("rdv_prestation", "")
    _rdv_ji = _ctx_intercept.get("rdv_jour", "")
    _rdv_hi = _ctx_intercept.get("rdv_heure", "")
    _context_complet = bool(_rdv_pi and _rdv_ji and _rdv_hi)

    _hist_text_i = " ".join(
        str(m.get("content", "")) for m in get_conversation_history(ctx_key)
    ).lower()
    _rdv_deja_recupere_i = "rdv trouvé" in _hist_text_i or "aucun rdv" in _hist_text_i
    _ctx_annulation_intercept = (
        any(m in message_user.lower() for m in ["annuler", "annulation", "supprimer", "effacer", "enlever", "mon rendez-vous"])
        and not _rdv_deja_recupere_i
    )

    _resp_lower_i = (response_text or "").lower()
    _est_phrase_attente = any(p in _resp_lower_i for p in PHRASES_ATTENTE_INTERDITES)

    # Variables pour C4 (GPT dit "confirmé" sans prendre_rdv)
    _mots_confirm        = ["confirmé", "confirmation", "sms envoyé", "vous recevez un sms"]
    _ctx_rdv_pris_check  = get_client_context(ctx_key).get("rdv_pris", False)
    _dispo_positive      = "disponibilité : libre" in _hist_text_i or "créneau libre" in _hist_text_i
    _gpt_annonce_confirm = any(m in _resp_lower_i for m in _mots_confirm)
    _ann_effect_c4       = get_client_context(ctx_key).get("annulation_effectuee", False)
    _ann_rdv_in_hist     = "annuler_rdv" in _hist_text_i
    _rdv_actif_in_hist   = "get_rdv_client_actif" in _hist_text_i

    # C1 : GPT a dit "je vais vérifier" au lieu d'appeler le tool → forcer l'appel direct
    if _est_phrase_attente and _context_complet:
        print(f"⚠️ [{nom_salon}] [INTERCEPTION] GPT a dit '{response_text[:60]}' → appel forcé")
        _dispo_result = appeler_verifier_disponibilite(_rdv_pi, _rdv_ji, _rdv_hi, telephone,
                                                       _ctx_intercept.get("rdv_coiffeur", ""),
                                                       ctx_key=ctx_key, salon=salon,
                                                       coiffeurs=coiffeurs, prestations=prestations)
        _fake_tool_id = f"forced_{int(now_paris().timestamp())}"
        add_assistant_message_with_tools(ctx_key, content=None, tool_calls=[{
            "id": _fake_tool_id, "type": "function",
            "function": {"name": "verifier_disponibilite", "arguments": json.dumps({"prestation": _rdv_pi, "jour": _rdv_ji, "heure": _rdv_hi})},
        }])
        add_tool_result(ctx_key, _fake_tool_id, _dispo_result)
        try:
            _msg_post = [{"role": "system", "content": sys_prompt}] + get_conversation_history(ctx_key)
            _msg_post = clean_messages(_msg_post)
            _resp2 = client_openai.chat.completions.create(
                model="gpt-4o-mini", messages=_msg_post, tools=TOOLS,
                tool_choice="none", temperature=0.1, max_tokens=100, stream=False,
            )
            session_tokens_input += _resp2.usage.prompt_tokens
            session_tokens_output += _resp2.usage.completion_tokens
            session_tokens_total += _resp2.usage.total_tokens
            response_text = _resp2.choices[0].message.content or ""
            print(f"⚠️ [INTERCEPTION] Réponse post-tool : {response_text[:80]!r}")
        except Exception as _e_int:
            print(f"⚠️ [INTERCEPTION] Erreur relance GPT : {_e_int}")
            response_text = _dispo_result  # fallback: dire directement le résultat

    # C3 : Réponse vide ET contexte complet → appel direct verifier_disponibilite sans GPT
    elif (not response_text or len(response_text.strip()) < 5) and _context_complet:
        print(f"⚠️ [{nom_salon}] [RÉPONSE VIDE] Appel direct verifier_disponibilite | prestation={_rdv_pi!r} jour={_rdv_ji!r} heure={_rdv_hi!r}")
        _dispo_result = appeler_verifier_disponibilite(_rdv_pi, _rdv_ji, _rdv_hi, telephone,
                                                       _ctx_intercept.get("rdv_coiffeur", ""),
                                                       ctx_key=ctx_key, salon=salon,
                                                       coiffeurs=coiffeurs, prestations=prestations)
        _fake_tool_id2 = f"forced_empty_{int(now_paris().timestamp())}"
        add_assistant_message_with_tools(ctx_key, content=None, tool_calls=[{
            "id": _fake_tool_id2, "type": "function",
            "function": {"name": "verifier_disponibilite", "arguments": json.dumps({"prestation": _rdv_pi, "jour": _rdv_ji, "heure": _rdv_hi})},
        }])
        add_tool_result(ctx_key, _fake_tool_id2, _dispo_result)
        try:
            _msg_post2 = [{"role": "system", "content": sys_prompt}] + get_conversation_history(ctx_key)
            _msg_post2 = clean_messages(_msg_post2)
            _resp3 = client_openai.chat.completions.create(
                model="gpt-4o-mini", messages=_msg_post2, tools=TOOLS,
                tool_choice="none", temperature=0.1, max_tokens=100, stream=False,
            )
            session_tokens_input += _resp3.usage.prompt_tokens
            session_tokens_output += _resp3.usage.completion_tokens
            session_tokens_total += _resp3.usage.total_tokens
            response_text = _resp3.choices[0].message.content or ""
            print(f"⚠️ [RÉPONSE VIDE] Réponse post-tool : {response_text[:80]!r}")
        except Exception as _e_empty:
            print(f"⚠️ [RÉPONSE VIDE] Erreur relance GPT : {_e_empty}")
            response_text = _dispo_result

    # C1-ANNULATION : GPT a dit "je vais récupérer" au lieu d'appeler get_rdv_client_actif
    elif _est_phrase_attente and _ctx_annulation_intercept:
        print(f"⚠️ [{nom_salon}] [INTERCEPTION ANNULATION] GPT a dit '{(response_text or '')[:60]}' → appel forcé")
        _rdv_result = process_tool_call("get_rdv_client_actif", {"telephone": telephone}, telephone,
                                        ctx_key=ctx_key, salon=salon, coiffeurs=coiffeurs, prestations=prestations)
        _fake_tool_id_ann = f"forced_ann_{int(now_paris().timestamp())}"
        add_assistant_message_with_tools(ctx_key, content=None, tool_calls=[{
            "id": _fake_tool_id_ann, "type": "function",
            "function": {"name": "get_rdv_client_actif", "arguments": json.dumps({"telephone": telephone})},
        }])
        add_tool_result(ctx_key, _fake_tool_id_ann, _rdv_result)
        try:
            _msg_post_ann = [{"role": "system", "content": sys_prompt}] + get_conversation_history(ctx_key)
            _msg_post_ann = clean_messages(_msg_post_ann)
            _resp_ann = client_openai.chat.completions.create(
                model="gpt-4o-mini", messages=_msg_post_ann, tools=TOOLS,
                tool_choice="none", temperature=0.1, max_tokens=100, stream=False,
            )
            session_tokens_input += _resp_ann.usage.prompt_tokens
            session_tokens_output += _resp_ann.usage.completion_tokens
            session_tokens_total += _resp_ann.usage.total_tokens
            response_text = _resp_ann.choices[0].message.content or ""
            print(f"⚠️ [INTERCEPTION ANNULATION] Réponse post-tool : {response_text[:80]!r}")
        except Exception as _e_ann:
            print(f"⚠️ [INTERCEPTION ANNULATION] Erreur relance GPT : {_e_ann}")
            response_text = _rdv_result

    # C4-CONFIRMÉ : GPT annonce "confirmé" sans avoir appelé prendre_rdv → forcer prendre_rdv
    elif _gpt_annonce_confirm and _dispo_positive and not _ctx_rdv_pris_check and _context_complet \
            and not _ann_effect_c4 \
            and not _ann_rdv_in_hist \
            and not _rdv_actif_in_hist:
        _ctx_c4 = get_client_context(ctx_key)
        _args_c4 = {
            "jour":           _ctx_c4.get("rdv_jour"),
            "heure":          _ctx_c4.get("rdv_heure"),
            "prestation":     (_ctx_c4.get("rdv_prestation") or "").strip(),
            "coiffeur":       _ctx_c4.get("rdv_coiffeur") or None,
            "client_nom":     _ctx_c4.get("prenom") or "",
            "type_client":    "homme",
            "avec_shampoing": bool(_ctx_c4.get("avec_shampoing", False)),
        }
        print(
            f"⚠️ [INTERCEPTION C4] GPT annonce confirmation sans prendre_rdv → forcer prendre_rdv | "
            f"jour={_args_c4['jour']} heure={_args_c4['heure']} "
            f"prestation={_args_c4['prestation']!r} coiffeur={_args_c4['coiffeur']!r}"
        )
        try:
            _rdv_forced_result = process_tool_call("prendre_rdv", _args_c4, telephone,
                                                    ctx_key=ctx_key, salon=salon,
                                                    coiffeurs=coiffeurs, prestations=prestations)
            _fake_id_c4 = f"forced_c4_{int(now_paris().timestamp())}"
            add_assistant_message_with_tools(ctx_key, content=None, tool_calls=[{
                "id": _fake_id_c4, "type": "function",
                "function": {"name": "prendre_rdv", "arguments": json.dumps(_args_c4)},
            }])
            add_tool_result(ctx_key, _fake_id_c4, _rdv_forced_result)
            print(f"✅ [{nom_salon}] [INTERCEPTION C4] prendre_rdv exécuté → {_rdv_forced_result[:80]!r}")
            _msg_voc = [{"role": "system", "content": sys_prompt}] + get_conversation_history(ctx_key)
            _msg_voc = clean_messages(_msg_voc)
            _resp_voc = client_openai.chat.completions.create(
                model="gpt-4o-mini", messages=_msg_voc, tools=TOOLS,
                tool_choice="none", temperature=0.1, max_tokens=100, stream=False,
            )
            session_tokens_input  += _resp_voc.usage.prompt_tokens
            session_tokens_output += _resp_voc.usage.completion_tokens
            session_tokens_total  += _resp_voc.usage.total_tokens
            response_text = _resp_voc.choices[0].message.content or "C'est confirmé ! Vous recevez un SMS. À bientôt !"
            print(f"✅ [INTERCEPTION C4] réponse vocale={response_text[:80]!r}")
        except Exception as _e_c4:
            print(f"⚠️ [INTERCEPTION C4] Erreur : {_e_c4}")
            response_text = "C'est confirmé ! Vous recevez un SMS de confirmation. À bientôt !"

    # Garde-fou ultime : si toujours vide, ne jamais raccrocher
    elif not response_text or len(response_text.strip()) < 5:
        print("⚠️ [GPT] Réponse vide/courte sans contexte complet — fallback vocale")
        response_text = "Je suis désolé, pouvez-vous répéter s'il vous plaît ?"

    # Garde-fou : phrase de fin en plein flow RDV
    PHRASES_FIN_FLOW = ["bonne journée", "au revoir", "à bientôt", "merci pour votre appel"]
    ctx_flow = get_client_context(ctx_key)
    if ctx_flow.get("rdv_en_cours") and not ctx_flow.get("rdv_pris") and any(p in (response_text or "").lower() for p in PHRASES_FIN_FLOW):
        if _dispo_positive and _context_complet:
            print(f"✅ [{nom_salon}] [FLOW] prendre_rdv forcé (phrase de fin interceptée)")
            try:
                _ctx_flow_rdv = get_client_context(ctx_key)
                _args_flow = {
                    "prestation": _ctx_flow_rdv.get("rdv_prestation", _rdv_pi),
                    "jour": _ctx_flow_rdv.get("rdv_jour", _rdv_ji),
                    "heure": _ctx_flow_rdv.get("rdv_heure", _rdv_hi),
                    "coiffeur": _ctx_flow_rdv.get("rdv_coiffeur", ""),
                }
                _flow_rdv_result = process_tool_call("prendre_rdv", _args_flow, telephone,
                                                      ctx_key=ctx_key, salon=salon,
                                                      coiffeurs=coiffeurs, prestations=prestations)
                _fake_id_flow = f"forced_flow_{int(now_paris().timestamp())}"
                add_assistant_message_with_tools(ctx_key, content=None, tool_calls=[{
                    "id": _fake_id_flow, "type": "function",
                    "function": {"name": "prendre_rdv", "arguments": json.dumps(_args_flow)},
                }])
                add_tool_result(ctx_key, _fake_id_flow, _flow_rdv_result)
                _msg_flow_voc = [{"role": "system", "content": sys_prompt}] + get_conversation_history(ctx_key)
                _msg_flow_voc = clean_messages(_msg_flow_voc)
                _resp_flow_voc = client_openai.chat.completions.create(
                    model="gpt-4o-mini", messages=_msg_flow_voc, tools=TOOLS,
                    tool_choice="none", temperature=0.1, max_tokens=100, stream=False,
                )
                session_tokens_input  += _resp_flow_voc.usage.prompt_tokens
                session_tokens_output += _resp_flow_voc.usage.completion_tokens
                session_tokens_total  += _resp_flow_voc.usage.total_tokens
                response_text = _resp_flow_voc.choices[0].message.content or "C'est confirmé ! Vous recevez un SMS. À bientôt !"
            except Exception as _e_flow:
                print(f"⚠️ [FLOW] Erreur lors du forçage prendre_rdv : {_e_flow}")
                response_text = "C'est confirmé ! Vous recevez un SMS de confirmation. À bientôt !"
        else:
            print(f"⚠️ [FLOW] Réponse de fin détectée en plein flow RDV — ignorée")
            response_text = "Je suis désolé, pouvez-vous répéter s'il vous plaît ?"

    # Garde-fou mémoire : si GPT redemande une info déjà dans le contexte RDV, la réinjecter
    _ctx_rdv = get_client_context(ctx_key)
    _resp_l = (response_text or "").lower()
    if _ctx_rdv.get("rdv_prestation") and any(k in _resp_l for k in ["quelle prestation", "quel type de prestation", "que souhaitez-vous comme"]):
        _prest = _ctx_rdv["rdv_prestation"]
        print(f"⚠️ [CONTEXTE] GPT redemande la prestation déjà connue ({_prest}) — corrigé")
        response_text = f"Très bien. Donc pour une {_prest}. Pour quel jour souhaitez-vous ?"
    elif _ctx_rdv.get("rdv_jour") and _ctx_rdv.get("rdv_heure") and any(k in _resp_l for k in ["quel jour", "pour quel jour", "quelle date", "quand souhaitez"]):
        _jour_c = _ctx_rdv["rdv_jour"]
        _heure_c = _ctx_rdv["rdv_heure"]
        print(f"⚠️ [CONTEXTE] GPT redemande le jour déjà connu ({_jour_c} {_heure_c}) — corrigé")
        response_text = f"Très bien. Je vérifie le créneau du {_jour_c} à {_heure_c}."

    # Garde-fou langue : si GPT répond en anglais hors contexte bilingue, forcer le français
    if not est_anglais and response_text:
        _mots_anglais_resp = ["appointment", "available", "sorry", "confirmed", "please", "thank you", "hello", "goodbye"]
        if sum(1 for _w in _mots_anglais_resp if _w in response_text.lower()) >= 2:
            print(f"⚠️ [LANGUE] Réponse anglaise détectée sans contexte bilingue — fallback français")
            response_text = "Je rencontre un problème technique. Pouvez-vous répéter s'il vous plaît ?"

    # Ajouter la réponse à l'historique
    add_to_history(ctx_key, "assistant", response_text)

    # Alerte patron si agent bloqué (pas de RDV après 3+ échanges)
    ctx2 = get_client_context(ctx_key)
    if not ctx2.get("rdv_pris"):
        nb_echecs = ctx2.get("nb_echecs", 0) + 1
        update_client_context(ctx_key, nb_echecs=nb_echecs)
        if nb_echecs >= 3:
            if SMS_ENABLED and twilio_client:
                try:
                    twilio_client.messages.create(
                        to=tel_salon or twilio_num, from_=twilio_num,
                        body=f"⚠️ [{nom_salon}] Agent bloqué avec {telephone}. Rappeler ce client !")
                    update_client_context(ctx_key, nb_echecs=0)
                except Exception:
                    pass
            else:
                logging.info(f"[SMS DÉSACTIVÉ] SMS alerte patron non envoyé à {tel_salon or twilio_num}")
                update_client_context(ctx_key, nb_echecs=0)
    else:
        update_client_context(ctx_key, nb_echecs=0)

    return response_text

# ====================================================
# ENDPOINTS RACINE
# ====================================================
@app.get("/")
def root():
    return {"status": "ok", "service": "Barbershop Agent S&B"}

@app.get("/health")
def health():
    return {"status": "ok"}

def salon_id_from_twilio() -> str:
    """Obsolète — utiliser get_salon_config(to_number).get('id') à la place."""
    return None

def _insert_call_stat(call_sid: str, telephone: str, salon_id: str = None) -> None:
    """Insère une ligne dans call_stats au début de chaque nouvel appel."""
    if not supabase or not call_sid:
        return
    try:
        if not salon_id:
            print("⚠️ [CALL_STATS] salon_id inconnu — insert ignoré")
            return
        supabase.table("call_stats").insert({
            "salon_id":   salon_id,
            "call_sid":   call_sid,
            "started_at": now_paris().isoformat(),
            "client_phone": telephone,
        }).execute()
        print(f"📊 [CALL_STATS] Appel démarré | CallSid={call_sid}")
    except Exception as _e:
        print(f"⚠️ [CALL_STATS] Erreur insert : {_e}")

def _deduire_motif_echec(ctx_key: str) -> str:
    """Déduit le motif d'échec depuis le contexte et l'historique de conversation."""
    ctx = get_client_context(ctx_key)
    if ctx.get("motif_echec_detecte"):
        return ctx["motif_echec_detecte"]
    hist = " ".join(
        str(m.get("content", "")) for m in get_conversation_history(ctx_key)
    ).lower()
    if "aucun créneau" in hist or "pas de créneau" in hist or "complet" in hist:
        return "pas_de_dispo"
    if "fermé" in hist and any(j in hist for j in ["lundi", "dimanche", "jour"]):
        return "fermé"
    return "abandon"

def _update_call_stat(call_sid: str, ctx_key: str, motif_echec: str = "abandon") -> None:
    """Met à jour la ligne call_stats à la fin de l'appel."""
    if not supabase or not call_sid:
        return
    try:
        ctx      = get_client_context(ctx_key)
        ended_at = now_paris()
        # Calculer la durée depuis started_at
        duration = None
        try:
            _row = supabase.table("call_stats").select("started_at")\
                .eq("call_sid", call_sid).limit(1).execute()
            if _row.data:
                from datetime import datetime as _dt
                _started = _dt.fromisoformat(_row.data[0]["started_at"])
                if _started.tzinfo is None:
                    _started = _started.replace(tzinfo=timezone.utc)
                duration = max(0, int((ended_at - _started).total_seconds()))
        except Exception:
            pass
        rdv_pris = bool(ctx.get("rdv_pris", False) or ctx.get("rdv_id_cree"))
        motif    = None if rdv_pris else _deduire_motif_echec(ctx_key) if motif_echec == "abandon" else motif_echec
        update_data = {
            "ended_at":        ended_at.isoformat(),
            "duration_seconds": duration,
            "rdv_pris":        rdv_pris,
            "rdv_id":          ctx.get("rdv_id_cree") or None,
            "prestation":      ctx.get("last_rdv_prestation") or None,
            "coiffeur":        ctx.get("last_rdv_coiffeur") or None,
            "client_phone":    ctx.get("telephone_appelant") or ctx_key.split("_")[0],
            "client_nouveau":  bool(ctx.get("client_nouveau", False)),
            "motif_echec":     motif,
            "nb_silences":     ctx.get("silences_total", 0),
        }
        supabase.table("call_stats").update(update_data).eq("call_sid", call_sid).execute()
        print(
            f"📊 [CALL_STATS] Appel terminé | CallSid={call_sid} | rdv_pris={rdv_pris} "
            f"| durée={duration}s | motif={motif or '—'}"
        )
    except Exception as _e:
        print(f"⚠️ [CALL_STATS] Erreur update : {_e}")

@app.post("/update-config")
async def sync_config(request: Request):
    """Met à jour la config d'un salon dans Supabase et invalide le cache."""
    try:
        data = await request.json()
        print(f"📥 [UPDATE-CONFIG] Payload reçu : {json.dumps(data, indent=2)}")

        twilio_phone = data.get("twilio_phone") or data.get("twilio_number") or ""
        if not twilio_phone:
            raise HTTPException(status_code=400, detail="twilio_phone requis pour identifier le salon")

        jours_map = {
            "Lundi": "lundi", "Mardi": "mardi",
            "Mercredi": "mercredi", "Jeudi": "jeudi",
            "Vendredi": "vendredi", "Samedi": "samedi",
            "Dimanche": "dimanche",
        }
        pause_debut = (
            data.get("lunch_break_start") or data.get("pause_debut")
            or data.get("break_start") or None
        )
        pause_fin = (
            data.get("lunch_break_end") or data.get("pause_fin")
            or data.get("break_end") or None
        )
        jours_ouverts = [jours_map.get(j, j.lower()) for j in data["open_days"]] \
            if data.get("open_days") else None

        salon_row = {"twilio_number": twilio_phone}
        if data.get("salon_name"):  salon_row["nom"]               = data["salon_name"]
        if data.get("address"):     salon_row["adresse"]            = data["address"]
        if data.get("open_time"):   salon_row["horaire_ouverture"]  = data["open_time"]
        if data.get("close_time"):  salon_row["horaire_fermeture"]  = data["close_time"]
        if pause_debut:             salon_row["pause_debut"]        = pause_debut
        if pause_fin:               salon_row["pause_fin"]          = pause_fin
        if jours_ouverts:           salon_row["jours_ouverts"]      = json.dumps(jours_ouverts)
        if data.get("webhook_url") or data.get("app_webhook_url"):
            salon_row["webhook_url"] = data.get("webhook_url") or data.get("app_webhook_url")
        if data.get("app_salon_id"):    salon_row["app_salon_id"]   = data["app_salon_id"]

        sid = None
        if supabase:
            try:
                existing = supabase.table("salon").select("id")\
                    .eq("twilio_number", twilio_phone).limit(1).execute()
                if existing.data:
                    sid = existing.data[0]["id"]
                    supabase.table("salon").update(salon_row).eq("id", sid).execute()
                else:
                    res = supabase.table("salon").insert(salon_row).execute()
                    sid = res.data[0]["id"] if res.data else None
                print(f"💾 [UPDATE-CONFIG] salon upserted | id={sid}")
            except Exception as e:
                print(f"❌ [UPDATE-CONFIG] Erreur upsert salon : {e}")

            # Coiffeurs
            staff_data = data.get("staff") or data.get("employees") or data.get("coiffeurs")
            if staff_data and sid:
                for s in staff_data:
                    nom = (s.get("full_name") or s.get("name") or
                           s.get("firstName") or s.get("first_name") or "")
                    if nom:
                        try:
                            supabase.table("employee").upsert({
                                "id": s.get("id"),
                                "salon_id": sid,
                                "full_name": nom,
                                "specialties": s.get("specialties") or s.get("role", ""),
                            }, on_conflict="id").execute()
                        except Exception as e:
                            print(f"⚠️ [UPDATE-CONFIG] Erreur upsert employee : {e}")

            # Prestations
            services_data = data.get("services") or data.get("prestations")
            if services_data and sid:
                for sv in services_data:
                    nom = sv.get("name") or sv.get("nom") or ""
                    if nom:
                        try:
                            supabase.table("service").upsert({
                                "id": sv.get("id"),
                                "salon_id": sid,
                                "name": nom,
                                "price": sv.get("price") or sv.get("prix") or 0,
                                "duration_minutes": sv.get("duration") or sv.get("duree") or 30,
                            }, on_conflict="id").execute()
                        except Exception as e:
                            print(f"⚠️ [UPDATE-CONFIG] Erreur upsert service : {e}")

        # Invalider le cache pour forcer rechargement au prochain appel
        invalidate_salon_cache(twilio_number=twilio_phone, salon_id=sid)

        # Mémoriser pour /sync-staff et /sync-services qui n'ont pas de salon_id dans leur payload
        if sid:
            _nom_uc = data.get("salon_name") or ""
            if not _nom_uc and supabase:
                try:
                    _nr = supabase.table("salon").select("nom").eq("id", sid).limit(1).execute()
                    _nom_uc = _nr.data[0].get("nom", "") if _nr.data else ""
                except Exception:
                    pass
            _last_sync_salon["salon_id"]      = sid
            _last_sync_salon["twilio_number"]  = twilio_phone
            _last_sync_salon["nom"]            = _nom_uc
            print(f"🔖 [UPDATE-CONFIG] last_sync_salon mis à jour → {_nom_uc} ({sid})")

        print(f"✅ [UPDATE-CONFIG] OK | twilio={twilio_phone} | salon_id={sid}")
        return {"status": "ok", "twilio_phone": twilio_phone, "salon_id": sid}

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [UPDATE-CONFIG] Erreur : {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/test-webhook")
async def test_webhook(twilio_phone: str = ""):
    """Teste les webhooks d'un salon identifié par twilio_phone (query param)."""
    import urllib.request as _urlreq2

    # Résoudre le salon depuis twilio_phone ou prendre le premier salon actif
    _webhook_url = ""
    _app_sid = ""
    if supabase:
        try:
            if twilio_phone:
                _sr = supabase.table("salon").select("webhook_url,app_salon_id")\
                    .eq("twilio_number", twilio_phone).limit(1).execute()
            else:
                _sr = supabase.table("salon").select("webhook_url,app_salon_id")\
                    .limit(1).execute()
            if _sr.data:
                _webhook_url = _sr.data[0].get("webhook_url") or ""
                _app_sid     = _sr.data[0].get("app_salon_id") or ""
        except Exception as _e_tw:
            print(f"⚠️ [TEST-WEBHOOK] Erreur résolution salon : {_e_tw}")

    def _call_webhook(payload_dict: dict) -> dict:
        _payload_bytes = json.dumps(payload_dict).encode("utf-8")
        _req = _urlreq2.Request(
            _webhook_url,
            data=_payload_bytes,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with _urlreq2.urlopen(_req, timeout=10) as _r:
                _body = _r.read().decode("utf-8", errors="replace")
                print(f"📡 [TEST-WEBHOOK] status={_r.status} | body={_body[:200]!r}")
                return {"status": _r.status, "body": _body[:500], "error": None}
        except Exception as _te:
            print(f"❌ [TEST-WEBHOOK] Erreur : {_te}")
            return {"status": None, "body": None, "error": f"{type(_te).__name__}: {_te}"}

    result = {
        "webhook_url": _webhook_url,
        "app_salon_id": _app_sid,
        "rdv": None,
        "annulation": None,
        "error": None,
    }

    if not _webhook_url:
        result["error"] = "webhook_url est vide — configurer via /update-config"
        return result
    if not _app_sid:
        result["error"] = "app_salon_id est vide — configurer via /update-config"
        return result

    # Test 1 — webhook RDV
    _rdv_payload = {
        "event": "rdv_created",
        "app_salon_id": _app_sid,
        "client_telephone": "+33600000000",
        "client_nom": "Test Client",
        "prestation": "Coupe homme",
        "jour": now_paris().date().isoformat(),
        "heure": "10:00",
        "coiffeur": "",
        "avec_shampoing": False,
        "source": "agent",
        "_test": True,
    }
    print(f"📡 [TEST-WEBHOOK] Test RDV | payload={json.dumps(_rdv_payload)}")
    result["rdv"] = _call_webhook(_rdv_payload)

    # Test 2 — webhook annulation
    _ann_payload = {
        "action": "cancelled",
        "appointment_id": "test-rdv-id-000",
        "app_salon_id": _app_sid,
        "source": "agent",
        "_test": True,
    }
    print(f"📡 [TEST-WEBHOOK] Test Annulation | payload={json.dumps(_ann_payload)}")
    result["annulation"] = _call_webhook(_ann_payload)

    return result


@app.post("/sync-staff")
async def sync_staff(request: Request):
    """Synchronise le personnel d'un salon dans Supabase et invalide le cache."""
    try:
        data = await request.json()
        print(f"📥 [SYNC-STAFF] Reçu : {data}")

        twilio_phone = data.get("twilio_phone") or data.get("twilio_number") or ""
        direct_sid   = data.get("salon_id") or ""
        staff_list   = data.get("staff") or data.get("employees") or []

        # Résoudre salon_id : priorité à salon_id direct, sinon depuis twilio_phone
        sid = None
        nom_salon_log = ""
        if direct_sid and supabase:
            try:
                _sr = supabase.table("salon").select("id,nom").eq("id", direct_sid).limit(1).execute()
                if _sr.data:
                    sid = _sr.data[0]["id"]
                    nom_salon_log = _sr.data[0].get("nom", direct_sid)
            except Exception as _e:
                print(f"⚠️ [SYNC-STAFF] Erreur résolution par salon_id : {_e}")
        if not sid and twilio_phone and supabase:
            try:
                res = supabase.table("salon").select("id,nom").eq("twilio_number", twilio_phone).limit(1).execute()
                if res.data:
                    sid = res.data[0]["id"]
                    nom_salon_log = res.data[0].get("nom", twilio_phone)
            except Exception as _e:
                print(f"⚠️ [SYNC-STAFF] Erreur résolution salon_id : {_e}")

        if not sid and _last_sync_salon.get("salon_id"):
            sid           = _last_sync_salon["salon_id"]
            nom_salon_log = _last_sync_salon.get("nom", sid)
            print(f"✅ [SYNC-STAFF] Salon identifié via last_sync : {nom_salon_log} (id={sid})")
        elif sid:
            print(f"✅ [SYNC-STAFF] Salon identifié : {nom_salon_log} (id={sid})")
        else:
            raise HTTPException(status_code=400, detail="salon_id inconnu — appeler /update-config avant /sync-staff")

        # Supprimer puis réinsérer
        try:
            supabase.table("employee").delete().eq("salon_id", sid).execute()
            print(f"🗑️ [SYNC-STAFF] Anciens coiffeurs supprimés pour salon_id={sid}")
        except Exception as e:
            print(f"⚠️ [SYNC-STAFF] Erreur delete : {e}")

        _TOUS_LES_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        nb_inserted = 0
        for s in staff_list:
            nom = (s.get("full_name") or s.get("name") or s.get("firstName") or "").strip().title()
            if not nom:
                continue

            _working_raw = s.get("working_days") or s.get("jours_travailles") or []
            if isinstance(_working_raw, str):
                try: _working_raw = json.loads(_working_raw)
                except Exception: _working_raw = []
            if _working_raw:
                _working_norm = [j.strip().lower() for j in _working_raw if j]
                jours_repos = [j for j in _TOUS_LES_JOURS if j not in _working_norm]
            else:
                _repos_raw = s.get("days_off") or s.get("jours_repos") or []
                if isinstance(_repos_raw, str):
                    try: _repos_raw = json.loads(_repos_raw)
                    except Exception: _repos_raw = []
                jours_repos = [j.strip().lower() for j in (_repos_raw or []) if j]

            heure_debut = s.get("work_start") or s.get("heure_debut") or "09:00"
            heure_fin   = s.get("work_end")   or s.get("heure_fin")   or "18:00"
            specialites = _normaliser_specialites(s.get("specialties") or s.get("role"))

            try:
                supabase.table("employee").insert({
                    "id":          str(uuid.uuid4()),
                    "salon_id":    sid,
                    "full_name":   nom,
                    "specialties": s.get("specialties", ""),
                    "days_off":    jours_repos if isinstance(jours_repos, list) else [],
                    "work_start":  str(heure_debut),
                    "work_end":    str(heure_fin),
                }).execute()
                nb_inserted += 1
                print(f"✅ [SYNC-STAFF] {nom} | repos: {jours_repos} | horaires: {heure_debut}-{heure_fin}")
            except Exception as e:
                print(f"⚠️ [SYNC-STAFF] Erreur insert {nom} : {e}")

        invalidate_salon_cache(twilio_number=twilio_phone, salon_id=sid)
        print(f"✅ [SYNC-STAFF] {nb_inserted} coiffeurs insérés | cache invalidé")
        return {"status": "ok", "coiffeurs": nb_inserted}
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [SYNC-STAFF] {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/sync-services")
async def sync_services(request: Request):
    """Synchronise les services d'un salon dans Supabase et invalide le cache."""
    try:
        data = await request.json()
        print(f"📥 [SYNC-SERVICES] Reçu : {data}")

        twilio_phone  = data.get("twilio_phone") or data.get("twilio_number") or ""
        direct_sid    = data.get("salon_id") or ""
        services_list = data.get("services") or data.get("prestations") or []

        # Résoudre salon_id : salon_id direct → twilio_phone → last_sync_salon
        sid = None
        nom_salon_log_sv = ""
        if direct_sid and supabase:
            try:
                _sr = supabase.table("salon").select("id,nom").eq("id", direct_sid).limit(1).execute()
                if _sr.data:
                    sid = _sr.data[0]["id"]
                    nom_salon_log_sv = _sr.data[0].get("nom", direct_sid)
            except Exception as _e:
                print(f"⚠️ [SYNC-SERVICES] Erreur résolution par salon_id : {_e}")
        if not sid and twilio_phone and supabase:
            try:
                res = supabase.table("salon").select("id,nom").eq("twilio_number", twilio_phone).limit(1).execute()
                if res.data:
                    sid = res.data[0]["id"]
                    nom_salon_log_sv = res.data[0].get("nom", twilio_phone)
            except Exception as _e:
                print(f"⚠️ [SYNC-SERVICES] Erreur résolution salon_id : {_e}")

        if not sid and _last_sync_salon.get("salon_id"):
            sid              = _last_sync_salon["salon_id"]
            nom_salon_log_sv = _last_sync_salon.get("nom", sid)
            print(f"✅ [SYNC-SERVICES] Salon identifié via last_sync : {nom_salon_log_sv} (id={sid})")
        elif sid:
            print(f"✅ [SYNC-SERVICES] Salon identifié : {nom_salon_log_sv} (id={sid})")
        else:
            raise HTTPException(status_code=400, detail="salon_id inconnu — appeler /update-config avant /sync-services")

        try:
            supabase.table("service").delete().eq("salon_id", sid).execute()
            print(f"🗑️ [SYNC-SERVICES] Anciens services supprimés pour salon_id={sid}")
        except Exception as e:
            print(f"⚠️ [SYNC-SERVICES] Erreur delete : {e}")

        nb_inserted = 0
        for sv in services_list:
            nom = sv.get("name") or sv.get("nom") or ""
            if not nom:
                continue
            try:
                supabase.table("service").insert({
                    "id": str(uuid.uuid4()),
                    "salon_id": sid,
                    "name": nom,
                    "price": sv.get("price") or 0,
                    "duration_minutes": sv.get("duration") or 30,
                }).execute()
                nb_inserted += 1
            except Exception as e:
                print(f"⚠️ [SYNC-SERVICES] Erreur insert {nom} : {e}")

        invalidate_salon_cache(twilio_number=twilio_phone, salon_id=sid)
        print(f"✅ [SYNC-SERVICES] {nb_inserted} services insérés | cache invalidé")
        return {"status": "ok", "prestations": nb_inserted}
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [SYNC-SERVICES] {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ====================================================
# ANNULATION RDV DEPUIS BASE44
# ====================================================
@app.post("/annuler-rdv")
async def annuler_rdv_base44(request: Request):
    try:
        data = await request.json()
        rdv_id = data.get("rdv_id")
        telephone = data.get("telephone")
        client_nom = data.get("client_nom")

        print(f"🗑️ [ANNULATION BASE44] rdv_id={rdv_id} tel={telephone}")

        # Annuler dans appointment
        try:
            supabase.table("appointment")\
                .update({"status": "annule"})\
                .eq("id", rdv_id)\
                .execute()
        except Exception as e:
            print(f"⚠️ [ANNULATION] appointment : {e}")

        # SMS au client — résoudre info salon depuis appointment
        if telephone and rdv_id:
            _nom_salon_ann = "le salon"
            _tel_salon_ann = ""
            _twilio_num_ann = ""
            try:
                _rdv_row = supabase.table("appointment").select("salon_id")\
                    .eq("id", rdv_id).limit(1).execute()
                if _rdv_row.data:
                    _sid_ann = _rdv_row.data[0].get("salon_id")
                    if _sid_ann:
                        _s_row = supabase.table("salon").select("nom,telephone,twilio_number")\
                            .eq("id", _sid_ann).limit(1).execute()
                        if _s_row.data:
                            _nom_salon_ann = _s_row.data[0].get("nom", "le salon")
                            _tel_salon_ann = _s_row.data[0].get("telephone", "")
                            _twilio_num_ann = _s_row.data[0].get("twilio_number", "")
            except Exception:
                pass
            prenom = (client_nom or "").split()[0] if client_nom else ""
            salutation = f"Bonjour {prenom}," if prenom else "Bonjour,"
            message = (
                f"{salutation} votre rendez-vous "
                f"au {_nom_salon_ann} a bien été annulé. "
                f"Pour reprendre un rendez-vous, "
                f"appelez-nous au {_tel_salon_ann or _twilio_num_ann}. "
                f"À bientôt !"
            )
            ok, _sms_sid = send_sms(telephone, message, from_number=_twilio_num_ann or None)
            print(f"📱 [ANNULATION] SMS envoyé : ok={ok}")

        return {"status": "ok", "message": "RDV annulé"}

    except Exception as e:
        print(f"❌ [ANNULATION BASE44] Erreur : {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ====================================================
# CORRECTION 1 — SYNC BIDIRECTIONNEL BASE44 → SUPABASE
# ====================================================
@app.post("/sync-appointment")
async def sync_appointment(request: Request):
    """
    Reçoit les RDV créés/modifiés/annulés depuis Base44 et les synchronise dans Supabase.
    Maintient la vision temps réel pour que l'agent voit les créneaux occupés.
    """
    try:
        _raw_body = await request.body()
        logging.info(f"📥 [SYNC-APPOINTMENT] Payload brut reçu : {_raw_body}")
        data = await request.json()
        action       = data.get("action", "created")
        base44_id    = data.get("appointment_id", "")
        rcvd_app_sid = data.get("app_salon_id", "")
        client_nom   = data.get("client_nom", "")
        client_tel   = data.get("client_telephone", "")
        prestation   = data.get("prestation", "")
        jour         = data.get("jour", "")     # YYYY-MM-DD
        heure        = data.get("heure", "")    # HH:MM
        coiffeur     = data.get("coiffeur", "")
        avec_shampoing = data.get("avec_shampoing", False)

        print(f"📥 [SYNC-APPOINTMENT] action={action} | jour={jour} | heure={heure} | coiffeur={coiffeur!r} | base44_id={base44_id!r}")

        _cancelled_actions = {"cancelled", "canceled", "deleted", "removed", "annule"}
        _action_lower = action.lower()
        _status_lower = (data.get("status") or "").lower()
        _is_cancel = _action_lower in _cancelled_actions or _status_lower in _cancelled_actions

        # Résoudre salon_id depuis app_salon_id reçu
        salon_id_eff = None
        if rcvd_app_sid and supabase:
            try:
                _res_sid = supabase.table("salon").select("id")\
                    .eq("app_salon_id", rcvd_app_sid).limit(1).execute()
                if _res_sid.data:
                    salon_id_eff = _res_sid.data[0]["id"]
                else:
                    print(f"⚠️ [SYNC-APPOINTMENT] app_salon_id inconnu: {rcvd_app_sid!r}")
                    return {"success": False, "error": f"app_salon_id inconnu: {rcvd_app_sid}"}
            except Exception as _e_sid:
                print(f"⚠️ [SYNC-APPOINTMENT] Erreur résolution salon_id : {_e_sid}")
        time_sql = heure + ":00" if heure and len(heure) == 5 else heure

        if action == "created" and not _is_cancel:
            # ── Rétablissement base44_id depuis payload Base44 (avant tout) ─────
            _b44_id_sync = (
                data.get("appointment_id") or
                data.get("base44_appointment_id") or ""
            ).strip()
            if _b44_id_sync:
                _tel_sync  = (data.get("client_telephone") or data.get("telephone") or "").strip()
                _jour_sync = (data.get("jour") or data.get("date") or "").strip()
                _heure_sync = (data.get("heure") or data.get("time") or "")[:5].strip()
                if _tel_sync and _jour_sync and _heure_sync:
                    _time_sync_sql = _heure_sync + ":00" if len(_heure_sync) == 5 else _heure_sync
                    try:
                        _rdv_sync = supabase.table("appointment")\
                            .select("id, base44_id")\
                            .eq("client_phone", _tel_sync)\
                            .eq("date", _jour_sync)\
                            .eq("time", _time_sync_sql)\
                            .is_("base44_id", "null")\
                            .execute()
                        if _rdv_sync.data:
                            supabase.table("appointment")\
                                .update({"base44_id": _b44_id_sync})\
                                .eq("id", _rdv_sync.data[0]["id"])\
                                .execute()
                            logging.info(
                                f"✅ [SYNC] base44_id rétabli "
                                f"via sync-appointment | "
                                f"rdv={_rdv_sync.data[0]['id']} | "
                                f"b44={_b44_id_sync}"
                            )
                    except Exception as _e_sync:
                        logging.warning(f"⚠️ [SYNC] Erreur rétablissement base44_id : {_e_sync}")

            # ── Anti-doublon : chercher RDV existant pour ce jour/heure(/coiffeur) ───
            is_doublon = False
            existing_id = None
            try:
                q = supabase.table("appointment")\
                    .select("id,base44_id").eq("date", jour).eq("time", time_sql)\
                    .neq("status", "cancelled").neq("status", "annule")
                if coiffeur:
                    q = q.eq("staff_name", coiffeur)
                res_dup = q.execute()
                if res_dup.data:
                    is_doublon = True
                    existing_id = res_dup.data[0]["id"]
                    print(f"🔁 [ANTI-DOUBLON] RDV déjà présent pour {jour} {heure} coiffeur={coiffeur!r} → id={existing_id}")
                    # Sauvegarder base44_id si manquant
                    if base44_id and not (res_dup.data[0].get("base44_id") or "").strip():
                        supabase.table("appointment").update({"base44_id": base44_id}).eq("id", existing_id).execute()
                        print(f"✅ [SYNC-APPOINTMENT] base44_id sauvegardé sur RDV existant | id={existing_id}")
            except Exception as e_dup:
                print(f"⚠️ [SYNC-APPOINTMENT] Erreur anti-doublon : {e_dup}")

            if is_doublon:
                return {"success": True, "doublon": True, "supabase_id": existing_id}

            # ── Insertion dans appointment ────────────────────────────────────────
            appt_row: dict = {
                "salon_id":    salon_id_eff,
                "client_name": client_nom or client_tel or "Inconnu",
                "client_phone": client_tel,
                "status":      "confirme",
                "date":        jour,
                "time":        time_sql,
                "service":     prestation,
                "staff_name":  coiffeur,
                "price":       0,
                "base44_id":   base44_id or None,
                "created_at":  datetime.now(timezone.utc).isoformat(),
                "notes":       json.dumps({"source": "app", "base44_id": base44_id}),
            }
            try:
                appt_res = supabase.table("appointment").insert(appt_row).execute()
                supabase_id = appt_res.data[0]["id"] if appt_res.data else None
            except Exception as e_notes:
                # Si la colonne notes n'existe pas → retry sans
                print(f"⚠️ [SYNC-APPOINTMENT] Insert avec notes échoué ({e_notes}) — retry sans notes")
                appt_row.pop("notes", None)
                appt_res = supabase.table("appointment").insert(appt_row).execute()
                supabase_id = appt_res.data[0]["id"] if appt_res.data else None

            # Retrouver et lier les RDV créés par l'agent vocal (base44_id absent)
            if base44_id and client_tel and jour and time_sql:
                try:
                    _ret = supabase.table("appointment").select("id")\
                        .eq("client_phone", client_tel).eq("date", jour).eq("time", time_sql)\
                        .eq("status", "confirme").is_("base44_id", "null").neq("id", supabase_id or "").execute()
                    for _row in (_ret.data or []):
                        supabase.table("appointment").update({"base44_id": base44_id}).eq("id", _row["id"]).execute()
                        print(f"✅ [SYNC-APPOINTMENT] base44_id rétabli sur RDV vocal | id={_row['id']}")
                except Exception as _e_ret:
                    print(f"⚠️ [SYNC-APPOINTMENT] Erreur rétablissement base44_id : {_e_ret}")

            print(f"✅ [SYNC-APPOINTMENT] Créé | supabase_id={supabase_id} | doublon=False")
            return {"success": True, "supabase_id": supabase_id, "doublon": False}

        elif action == "updated":
            # ── Recherche par jour+heure+coiffeur (fallback : base44_id dans notes) ─
            target_id = None
            try:
                q_upd = supabase.table("appointment").select("id, notes")\
                    .eq("date", jour).eq("time", time_sql)
                if coiffeur:
                    q_upd = q_upd.eq("staff_name", coiffeur)
                res_upd = q_upd.execute()
                if res_upd.data:
                    target_id = res_upd.data[0]["id"]
            except Exception as e_find:
                print(f"⚠️ [SYNC-APPOINTMENT] Erreur recherche update : {e_find}")

            if target_id:
                supabase.table("appointment").update({
                    "date": jour, "time": time_sql,
                    "staff_name": coiffeur, "service": prestation,
                }).eq("id", target_id).execute()
                print(f"✅ [SYNC-APPOINTMENT] Mis à jour | supabase_id={target_id}")
                return {"success": True, "supabase_id": target_id, "doublon": False}
            else:
                print(f"⚠️ [SYNC-APPOINTMENT] RDV non trouvé pour update — base44_id={base44_id!r}")
                return {"success": False, "error": "RDV non trouvé"}

        elif _is_cancel:
            # ── Recherche par base44_id d'abord (plus précis) ─────────────────────
            target_id = None
            if base44_id:
                try:
                    res_b44 = supabase.table("appointment").select("id")\
                        .eq("base44_id", base44_id).execute()
                    if res_b44.data:
                        target_id = res_b44.data[0]["id"]
                        print(f"🔍 [SYNC-APPOINTMENT] RDV trouvé par base44_id={base44_id!r} → id={target_id}")
                except Exception as e_b44:
                    print(f"⚠️ [SYNC-APPOINTMENT] Erreur recherche base44_id : {e_b44}")

            # ── Fallback : recherche par jour+heure+coiffeur ──────────────────────
            if not target_id and jour and time_sql:
                try:
                    q_can = supabase.table("appointment").select("id")\
                        .eq("date", jour).eq("time", time_sql)\
                        .neq("status", "cancelled").neq("status", "annule")
                    if coiffeur:
                        q_can = q_can.eq("staff_name", coiffeur)
                    res_can = q_can.execute()
                    if res_can.data:
                        target_id = res_can.data[0]["id"]
                        print(f"🔍 [SYNC-APPOINTMENT] RDV trouvé par date/heure | id={target_id}")
                except Exception as e_can:
                    print(f"⚠️ [SYNC-APPOINTMENT] Erreur recherche cancel : {e_can}")

            if target_id:
                supabase.table("appointment").update({"status": "annule"}).eq("id", target_id).execute()
                print(f"🗑️ [SYNC] RDV annulé depuis Base44 | action={action!r} | id={target_id}")
                return {"success": True, "supabase_id": target_id}
            else:
                print(f"⚠️ [SYNC-APPOINTMENT] RDV non trouvé pour cancel — action={action!r} base44_id={base44_id!r}")
                return {"success": False, "error": "RDV non trouvé"}

        else:
            return {"success": False, "error": f"Action inconnue : {action}"}

    except Exception as e:
        print(f"❌ [SYNC-APPOINTMENT] Erreur : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/annuler-rdv-base44")
async def annuler_rdv_base44(request: Request):
    """
    Annule un RDV dans Supabase depuis Base44 directement.
    Body JSON : { "appointment_id": "uuid_supabase", "base44_id": "xxx" }
    """
    try:
        data = await request.json()
        appointment_id = (data.get("appointment_id") or "").strip()
        base44_id_in   = (data.get("base44_id") or "").strip()

        if not appointment_id and not base44_id_in:
            raise HTTPException(status_code=400, detail="appointment_id ou base44_id requis")

        target_id = None
        if appointment_id:
            target_id = appointment_id
        elif base44_id_in:
            try:
                res = supabase.table("appointment").select("id").eq("base44_id", base44_id_in).execute()
                if res.data:
                    target_id = res.data[0]["id"]
            except Exception as _e:
                print(f"⚠️ [ANNULER-RDV-BASE44] Erreur lookup base44_id : {_e}")

        if not target_id:
            print(f"⚠️ [ANNULER-RDV-BASE44] RDV introuvable | appointment_id={appointment_id!r} base44_id={base44_id_in!r}")
            return {"success": False, "error": "RDV non trouvé"}

        supabase.table("appointment").update({"status": "annule"}).eq("id", target_id).execute()
        print(f"🗑️ [ANNULER-RDV-BASE44] RDV annulé | id={target_id}")
        return {"success": True, "supabase_id": target_id}

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [ANNULER-RDV-BASE44] Erreur : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/force-sync-annulation")
async def force_sync_annulation(request: Request):
    """
    Base44 appelle cet endpoint quand un RDV est annulé depuis le planning.
    Body JSON : { "base44_id": "xxx", "app_salon_id": "xxx" }
    Utilisable même si /sync-appointment ne reçoit pas de webhook.
    """
    try:
        body = await request.json()
        base44_id_fs = (body.get("base44_id") or "").strip()
        app_salon_id_fs = (body.get("app_salon_id") or "").strip()

        logging.info(f"📥 [FORCE-SYNC] Payload reçu : base44_id={base44_id_fs!r} app_salon_id={app_salon_id_fs!r}")

        if not base44_id_fs:
            return {"error": "base44_id manquant"}

        # Recherche par base44_id
        result = supabase.table("appointment").select("id, status")\
            .eq("base44_id", base44_id_fs).execute()

        # Fallback : recherche par app_salon_id si base44_id inconnu
        if not result.data and app_salon_id_fs:
            try:
                _s = supabase.table("salon").select("id").eq("app_salon_id", app_salon_id_fs).limit(1).execute()
                if _s.data:
                    _sid = _s.data[0]["id"]
                    result = supabase.table("appointment").select("id, status")\
                        .eq("salon_id", _sid).eq("base44_id", base44_id_fs).execute()
            except Exception:
                pass

        if not result.data:
            logging.info(f"⚠️ [FORCE-SYNC] RDV introuvable | base44_id={base44_id_fs!r}")
            return {"error": "RDV introuvable", "base44_id": base44_id_fs}

        rdv_id = result.data[0]["id"]
        supabase.table("appointment").update({"status": "annule"}).eq("id", rdv_id).execute()
        logging.info(
            f"✅ [FORCE-SYNC] RDV annulé | "
            f"base44_id={base44_id_fs} | "
            f"supabase_id={rdv_id}"
        )

        return {
            "success": True,
            "message": "RDV annulé dans Supabase",
            "rdv_id": rdv_id,
        }

    except Exception as e:
        logging.info(f"❌ [FORCE-SYNC] Erreur : {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ====================================================
# CORRECTION 3 — DISPONIBILITÉS TEMPS RÉEL (Base44)
# ====================================================
@app.get("/dispos")
async def get_dispos(jour: str, salon_id: str = None, twilio_phone: str = None):
    """
    Retourne tous les créneaux disponibles pour un jour donné, pour tous les coiffeurs.
    Usage : GET /dispos?jour=2026-05-19&twilio_phone=+336...
    Consommable depuis Base44 pour afficher le planning en temps réel.
    """
    try:
        if not jour:
            raise HTTPException(status_code=400, detail="Paramètre 'jour' manquant")

        # Résoudre salon depuis twilio_phone ou salon_id
        _salon_dispos = None
        _sid_dispos = salon_id
        if twilio_phone:
            _salon_dispos = get_salon_config(twilio_phone)
            if _salon_dispos:
                _sid_dispos = _salon_dispos.get("id")
        elif _sid_dispos and supabase:
            try:
                _sr = supabase.table("salon").select("*").eq("id", _sid_dispos).limit(1).execute()
                if _sr.data:
                    _salon_dispos = _sr.data[0]
            except Exception:
                pass

        _ouv = (_salon_dispos or {}).get("horaire_ouverture", "09:00")
        _fer = (_salon_dispos or {}).get("horaire_fermeture", "18:00")
        ouv_min  = parse_hhmm_en_minutes(_ouv)
        ferm_min = parse_hhmm_en_minutes(_fer)
        _coiffeurs_dispos = get_coiffeurs(_sid_dispos) if _sid_dispos else []

        # Générer tous les créneaux de 30 en 30
        creneaux_bruts = []
        cur = ouv_min
        while cur + 30 <= ferm_min:
            h = f"{cur // 60:02d}:{cur % 60:02d}"
            creneaux_bruts.append(h)
            cur += 30

        # Pour chaque créneau et chaque coiffeur, vérifier la dispo
        creneaux_result = []
        for heure in creneaux_bruts:
            dispo = est_creneau_disponible_v2(jour, heure, coiffeurs=_coiffeurs_dispos, salon_id=_sid_dispos)
            coiffeurs_libres = dispo["coiffeurs_libres"]
            if _coiffeurs_dispos:
                for c in _coiffeurs_dispos:
                    creneaux_result.append({
                        "heure":       heure,
                        "coiffeur":    c["nom"],
                        "disponible":  c["nom"] in coiffeurs_libres,
                    })
            else:
                creneaux_result.append({
                    "heure":      heure,
                    "coiffeur":   "",
                    "disponible": dispo["disponible"],
                })

        return {"jour": jour, "creneaux": creneaux_result}

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [DISPO] Erreur : {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ====================================================
# ENDPOINT PRINCIPAL
# ====================================================
@app.post("/appel", response_class=PlainTextResponse)
def handle_appel(
    From: str = Form(default=""),
    Called: str = Form(default=""),
    To: str = Form(default=""),
    SpeechResult: str = Form(default=""),
    CallSid: str = Form(default=""),
):
    twiml = VoiceResponse()

    # Résoudre le salon depuis le numéro Twilio appelé
    to_number = To or Called or ""
    salon = get_salon_config(to_number) if to_number else None
    if not salon:
        twiml.say(
            "Ce numéro n'est pas configuré. Veuillez contacter le support.",
            language="fr-FR", voice="Polly.Lea",
        )
        twiml.hangup()
        return str(twiml)

    salon_id   = salon["id"]
    nom_salon  = salon.get("nom", "le salon")
    coiffeurs  = get_coiffeurs(salon_id)
    prestations = get_prestations(salon_id)
    print(f"📞 [APPEL] salon={nom_salon} | to={to_number} | Coiffeurs={len(coiffeurs)} | Prestations={len(prestations)}")

    # Charger le contexte client immédiatement (pour accueil personnalisé)
    _from_early = From or ""
    _ctx_key_early = f"{_from_early}_{salon_id}" if _from_early else ""
    try:
        if _from_early and _from_early.lower() not in ("anonymous", "blocked", "unknown", ""):
            _client_early = get_or_create_client(_from_early)
            if _client_early.get("nom"):
                _prenom_early = _client_early["nom"].split()[0]
                _rdvs_early = get_rdv_client(_client_early.get("id", ""), salon_id=salon_id)
                update_client_context(
                    _ctx_key_early,
                    prenom=_prenom_early,
                    client_id=_client_early.get("id"),
                    nb_visites=_client_early.get("nb_visites", 0),
                    derniere_visite=_rdvs_early[-1] if _rdvs_early else None,
                )
                print(f"👤 [ACCUEIL] Client reconnu : {_prenom_early} ({_client_early.get('nb_visites',0)} visites)")
    except Exception as _e_early:
        print(f"⚠️ [ACCUEIL] Erreur chargement client : {_e_early}")

    # Anti-spam : bloquer si +10 appels en 24h
    def est_spam(tel: str) -> bool:
        if not supabase or not tel:
            return False
        try:
            hier = (now_paris() - timedelta(days=1)).isoformat()
            result = supabase.table("usage_logs").select("id")\
                .eq("twilio_number", tel).gte("created_at", hier).execute()
            return len(result.data or []) > 10
        except Exception:
            return False

    telephone_appelant = From or ""
    ctx_key = f"{telephone_appelant}_{salon_id}" if telephone_appelant else f"_{salon_id}"

    # Numéro masqué ou anonyme
    if not telephone_appelant or telephone_appelant.lower() in ("", "anonymous", "unknown"):
        twiml.say(
            "Bonjour ! Pour prendre rendez-vous, merci de rappeler sans masquer votre numéro "
            "afin qu'on puisse vous envoyer la confirmation. À bientôt !",
            language="fr-FR", voice="Polly.Lea",
        )
        twiml.hangup()
        return str(twiml)

    if est_spam(telephone_appelant):
        twiml.say("Ce numéro a été temporairement suspendu.", language="fr-FR", voice="Polly.Lea")
        twiml.hangup()
        return str(twiml)

    # ── CORRECTION 1 : Détection nouvel appel via CallSid ────────────────────
    _ctx_early2 = get_client_context(ctx_key)
    _stored_sid = _ctx_early2.get("call_sid", "")
    _is_new_call = bool(CallSid and CallSid != _stored_sid)
    if _is_new_call:
        _preserved = {
            k: _ctx_early2.get(k)
            for k in ("prenom", "client_id", "nb_visites", "derniere_visite", "nom")
            if _ctx_early2.get(k)
        }
        _preserved["call_sid"]     = CallSid
        _preserved["silences"]     = 0
        _preserved["accueil_joue"] = False
        client_context[ctx_key] = _preserved
        conversation_history[ctx_key] = []
        print(f"📞 [NOUVEL APPEL] CallSid={CallSid} | salon={nom_salon} | ctx_key={ctx_key} | silences=0 | accueil_joue=False | reset=True")
        _insert_call_stat(CallSid, telephone_appelant, salon_id=salon_id)
    else:
        update_client_context(ctx_key, call_sid=CallSid)
        print(f"📞 [MÊME APPEL] CallSid={CallSid} | ctx_key={ctx_key} | contexte préservé")

    # Log diagnostic complet
    _ctx_diag = get_client_context(ctx_key)
    print(f"🔍 [DEBUG APPEL] CallSid={CallSid} | stored_sid={_stored_sid or 'vide'} | reset={_is_new_call} | silences={_ctx_diag.get('silences', 0)} | accueil_joue={_ctx_diag.get('accueil_joue', False)}")

    HINTS = (
        "rendez-vous, coupe, couleur, brushing, shampoing, annuler, demain, "
        "lundi, mardi, mercredi, jeudi, vendredi, samedi, bonjour, oui, non, "
        "merci, au revoir, barbe, dégradé, soin, balayage, mèches, prénom, heure"
    )
    if not SpeechResult:
        import random as _rand
        _ctx_sil = get_client_context(ctx_key)
        hist_en_cours = get_conversation_history(ctx_key)
        en_conversation = len(hist_en_cours) > 0
        accueil_joue = _ctx_sil.get("accueil_joue", False)

        if not accueil_joue:
            update_client_context(ctx_key, accueil_joue=True, silences=0)
            ctx_accueil = _ctx_sil
            prenom_connu = ctx_accueil.get("prenom", "")
            nb_visites_connu = ctx_accueil.get("nb_visites", 0)
            if prenom_connu and nb_visites_connu > 0:
                accueils = [
                    f"Bonjour {prenom_connu}, ravi de vous retrouver. Comment puis-je vous aider ?",
                    f"Bonjour {prenom_connu}, bienvenue chez {nom_salon}. Que puis-je faire pour vous aujourd'hui ?",
                    f"Bonjour {prenom_connu}, nous sommes ravis de vous retrouver. Que puis-je faire pour vous ?",
                    f"Bonjour {prenom_connu}, toujours un plaisir. Comment puis-je vous aider ?",
                ]
            else:
                accueils = [
                    f"Bonjour et bienvenue chez {nom_salon}, comment puis-je vous aider ?",
                    f"Bonjour, salon {nom_salon}, que puis-je faire pour vous ?",
                    f"Bonjour, vous êtes bien chez {nom_salon}, comment puis-je vous aider ?",
                ]
            message_accueil = _rand.choice(accueils)
            print(f"📡 [GATHER] accueil initial | action=/appel POST | speech_timeout=auto timeout=10")
            gather = twiml.gather(
                input="speech", action="/appel", method="POST",
                language="fr-FR", speech_timeout="auto",
                speech_model="phone_call", timeout=10, hints=HINTS,
            )
            gather.say(message_accueil, language="fr-FR", voice="Polly.Lea", barge_in=False)
            return str(twiml)

        nb_silences = _ctx_sil.get("silences", 0) + 1
        _silences_total = _ctx_sil.get("silences_total", 0) + 1
        update_client_context(ctx_key, silences=nb_silences, silences_total=_silences_total)
        print(f"🔇 [SILENCE] {nb_silences}/3 (total={_silences_total}) | en_conversation={en_conversation} | ctx_key={ctx_key}")

        if nb_silences >= 3:
            twiml.say(
                "Je ne vous entends pas bien, n'hésitez pas à rappeler. À bientôt !",
                language="fr-FR", voice="Polly.Lea",
            )
            twiml.hangup()
            _call_sid_sil = get_client_context(ctx_key).get("call_sid", CallSid)
            _update_call_stat(_call_sid_sil, ctx_key, motif_echec="silence")
            update_client_context(ctx_key, silences=0, accueil_joue=False)
            print(f"📵 [FIN APPEL] raison=3_silences_consecutifs | ctx_key={ctx_key}")
            return str(twiml)

        if en_conversation:
            msgs_relance = [
                "Je ne vous ai pas bien entendu, pouvez-vous répéter ?",
                "Désolé, je n'entends pas bien. Pouvez-vous répéter s'il vous plaît ?",
                "Excusez-moi, pouvez-vous répéter votre réponse ?",
            ]
            _msg_relance = _rand.choice(msgs_relance)
            print(f"📡 [GATHER] silence {nb_silences}/3 mid-conv | action=/appel POST | speech_timeout=2 timeout=5")
            gather = twiml.gather(
                input="speech", action="/appel", method="POST",
                language="fr-FR", speech_timeout="2",
                speech_model="phone_call", timeout=5, hints=HINTS,
            )
            gather.say(_msg_relance, language="fr-FR", voice="Polly.Lea", barge_in=False)
            return str(twiml)

        _prenom_reac = _ctx_sil.get("prenom", "")
        _visites_reac = _ctx_sil.get("nb_visites", 0)
        if _prenom_reac and _visites_reac > 0:
            _accueils_retry = [
                f"Bonjour {_prenom_reac}, ravi de vous retrouver. Que puis-je faire pour vous ?",
                f"Bonjour {_prenom_reac}, je vous écoute, comment puis-je vous aider ?",
            ]
        else:
            _accueils_retry = [
                f"Bonjour et bienvenue chez {nom_salon}. Comment puis-je vous aider ?",
                f"Bonjour, vous êtes bien chez {nom_salon}. Je vous écoute.",
            ]
        _msg_reaccueil = _rand.choice(_accueils_retry)
        print(f"📡 [GATHER] silence {nb_silences}/3 post-accueil — rejouer accueil | action=/appel POST | speech_timeout=2 timeout=5")
        gather = twiml.gather(
            input="speech", action="/appel", method="POST",
            language="fr-FR", speech_timeout="2",
            speech_model="phone_call", timeout=5, hints=HINTS,
        )
        gather.say(_msg_reaccueil, language="fr-FR", voice="Polly.Lea", barge_in=False)
        return str(twiml)

    # ── SpeechResult non vide → remettre le compteur de silences à 0 ──────────
    telephone = telephone_appelant
    update_client_context(ctx_key, silences=0)

    # ── Vérification pause déjeuner pré-GPT ──────────────────────────────────
    _speech_pause = SpeechResult.lower()
    _m_h_pause = re.search(r'\b(\d{1,2})h(\d{2})?\b', _speech_pause) \
                 or re.search(r'\b(\d{1,2})\s*heures?\b', _speech_pause)
    _is_midi_pause   = bool(re.search(r'\bmidi\b', _speech_pause))
    _is_minuit_pause = bool(re.search(r'\bminuit\b', _speech_pause))
    if _m_h_pause or _is_midi_pause or _is_minuit_pause:
        try:
            if _is_midi_pause and not _m_h_pause:
                _hh_pause, _mm_pause = 12, "00"
            elif _is_minuit_pause and not _m_h_pause:
                _hh_pause, _mm_pause = 0, "00"
            else:
                _hh_pause = int(_m_h_pause.group(1))
                _mm_pause = _m_h_pause.group(2) or "00"
            _heure_pause_str = f"{_hh_pause:02d}:{_mm_pause}"
            _pd_pause = salon.get("pause_debut")
            _pf_pause = salon.get("pause_fin")
            if _pd_pause and _pf_pause:
                _h_min_p  = parse_hhmm_en_minutes(_heure_pause_str)
                _pd_min_p = parse_hhmm_en_minutes(_pd_pause)
                _pf_min_p = parse_hhmm_en_minutes(_pf_pause)
                if _pd_min_p <= _h_min_p < _pf_min_p:
                    _msg_pause_early = (
                        f"Le salon est en pause déjeuner de {_pd_pause} à {_pf_pause}. "
                        f"Je peux vous proposer un créneau avant {_pd_pause} ou à partir de {_pf_pause}."
                    )
                    print(f"⏸️ [{nom_salon}] [PAUSE PRÉ-GPT] heure={_heure_pause_str} dans pause {_pd_pause}-{_pf_pause}")
                    _gather_pause = twiml.gather(
                        input="speech", action="/appel", method="POST",
                        language="fr-FR", speech_timeout="2",
                        speech_model="phone_call", timeout=5, hints=HINTS,
                    )
                    _gather_pause.say(_msg_pause_early, language="fr-FR", voice="Polly.Lea", barge_in=False)
                    return str(twiml)
        except Exception:
            pass

    response_text = run_agent(
        SpeechResult, telephone,
        ctx_key=ctx_key, salon=salon,
        coiffeurs=coiffeurs, prestations=prestations,
    )

    # Seules phrases EXPLICITES de congé — combinaisons uniquement, jamais un mot seul
    PHRASES_FIN_CLIENT = [
        "au revoir",
        "merci au revoir",
        "bonne journée",
        "bonne soirée",
        "bonne continuation",
        "à la prochaine",
        "c'est tout merci",
        "ok merci au revoir",
        "merci bye",
    ]
    import random as _rand2
    REPONSES_FIN = [
        "À très bientôt ! Bonne journée à vous !",
        "Avec plaisir ! À bientôt chez nous !",
        "Merci à vous ! Passez une excellente journée !",
        "Au revoir ! On vous attend avec plaisir !",
        "À bientôt ! Prenez soin de vous !",
        "Bonne journée ! À très vite !",
    ]

    speech_lower = SpeechResult.lower().strip()
    nb_mots = len(speech_lower.split())

    # Silence ou message vide → jamais de fin d'appel
    if not speech_lower:
        pass  # continuer vers le gather normal

    # Mots seuls qui ne déclenchent JAMAIS une fin d'appel
    MOTS_AMBIGUS = {"merci", "ok", "oui", "non", "voilà", "voila", "d'accord",
                    "bien", "super", "parfait", "ciao", "bye", "salut", "à bientôt"}
    # Un message court (≤ 3 mots) OU contenant un mot ambigu isolé → jamais fin d'appel
    _speech_clean = speech_lower.strip(".,!? ")
    est_mot_seul_ambigu = (
        nb_mots <= 3
        or _speech_clean in MOTS_AMBIGUS
        or any(_speech_clean == m or _speech_clean.startswith(m + " ") or _speech_clean.endswith(" " + m)
               for m in MOTS_AMBIGUS)
    )

    # Un horaire (14h, 10h30, 15 heures…) n'est jamais une fin d'appel
    import re as _re
    contient_horaire = bool(_re.search(r'\b\d{1,2}h\d{0,2}\b|\d{1,2}\s*heures?\b', speech_lower))

    # Mots interrogatifs ou contextuels → pas un congé
    mots_question = ["?", "quoi", "autre", "avez", "faites",
                     "proposez", "encore", "aussi", "plus", "heure",
                     "rendez", "créneau", "disponible", "semaine"]
    est_question = any(m in speech_lower for m in mots_question)

    # Fin d'appel UNIQUEMENT si : "au revoir" ET "merci" présents ensemble (combinaison explicite)
    # Seul le CLIENT peut terminer l'appel — jamais sur un mot seul
    _contient_au_revoir = "au revoir" in speech_lower
    _contient_merci = "merci" in speech_lower
    _fin_explicite = _contient_au_revoir and _contient_merci
    _en_attente_ann = get_client_context(ctx_key).get("en_attente_confirmation_annulation", False)
    est_fin_client = (
        _fin_explicite
        and not est_question
        and not contient_horaire
        and not est_mot_seul_ambigu
        and not _en_attente_ann
    )

    if est_fin_client:
        reponse_fin = _rand2.choice(REPONSES_FIN)
        twiml.say(reponse_fin, language="fr-FR", voice="Polly.Lea")
        twiml.hangup()
        _call_sid_fin = get_client_context(ctx_key).get("call_sid", CallSid)
        _update_call_stat(_call_sid_fin, ctx_key, motif_echec="abandon")
        print(f"📵 [FIN APPEL] raison=au_revoir_merci | speech='{speech_lower[:60]}' | ctx_key={ctx_key}")
        return str(twiml)

    # Message d'attente pré-outil (évite silence Twilio)
    ctx_post = get_client_context(ctx_key)
    msg_attente = ctx_post.pop("message_attente", None)
    if msg_attente:
        update_client_context(ctx_key)  # flush (pop already done on dict)
    texte_final = (msg_attente + " " + response_text) if msg_attente else response_text

    # Construire les hints adaptés au contexte de la réponse
    _resp_lower = (response_text or "").lower()
    ctx_gather = get_client_context(ctx_key)

    HINTS_HEURES = (
        "neuf heures, dix heures, onze heures, midi, treize heures, quatorze heures, "
        "quinze heures, seize heures, dix-sept heures, dix-huit heures, "
        "9h, 10h, 11h, 12h, 13h, 14h, 15h, 16h, 17h, 18h, "
        "9h30, 10h30, 11h30, 14h30, 15h30, 16h30, 17h30"
    )
    _jours_ouverts_salon = salon.get("jours_ouverts") or ["mardi","mercredi","jeudi","vendredi","samedi"]
    _auj_hints = now_paris().date()
    _jours_ouverts_lower_h = [j.lower() for j in _jours_ouverts_salon]
    _dates_hints = []
    _dh = _auj_hints + timedelta(days=1)
    while len(_dates_hints) < 14:
        if NOMS_JOURS[_dh.weekday()].lower() in _jours_ouverts_lower_h:
            _dates_hints.append(f"{NOMS_JOURS[_dh.weekday()]} {_dh.day}")
        _dh += timedelta(days=1)
    HINTS_JOURS = (
        "lundi, mardi, mercredi, jeudi, vendredi, samedi, "
        "demain, après-demain, cette semaine, semaine prochaine, "
        + ", ".join(_dates_hints)
    )
    HINTS_SHAMPOING = "oui, non, avec, sans, volontiers, pas de shampoing"

    question_heure = any(k in _resp_lower for k in [
        "quelle heure", "pour quelle heure", "à quelle heure", "quel créneau", "quel horaire"
    ])
    question_jour = any(k in _resp_lower for k in [
        "quel jour", "quelle date", "quand souhaitez", "pour quel jour", "quelle journée"
    ])
    question_shampoing = "shampoing" in _resp_lower and not ctx_gather.get("shampoing_repondu")
    question_prestation = any(k in _resp_lower for k in [
        "quelle prestation", "quel service", "que souhaitez-vous", "souhaitez-vous comme"
    ])
    question_annulation = any(k in _resp_lower for k in [
        "souhaitez-vous vraiment", "voulez-vous annuler", "confirmer l'annulation",
        "annuler votre rendez-vous", "bien annuler",
    ])

    hints_extra = []
    if question_shampoing:
        hints_extra.append(HINTS_SHAMPOING)
    if question_heure:
        hints_extra.append(HINTS_HEURES)
    if question_jour:
        hints_extra.append(HINTS_JOURS)
    if question_prestation and prestations:
        prest_hints = ", ".join(p.get("name", "") for p in prestations if p.get("name"))
        if prest_hints:
            hints_extra.append(prest_hints)
    if question_annulation:
        hints_extra.append("oui, non, confirmer, annuler, oui confirmer, non garder, oui je confirme")

    hints_gather = HINTS + (", " + ", ".join(hints_extra) if hints_extra else "")

    _gather_ctx = (
        f"shampoing={question_shampoing} heure={question_heure} "
        f"jour={question_jour} prestation={question_prestation} annulation={question_annulation}"
    )
    print(f"📡 [GATHER] main | action=/appel POST | speech_timeout=2 timeout=5 | {_gather_ctx} | hints_len={len(hints_gather)}c")

    gather = twiml.gather(
        input="speech",
        action="/appel",
        method="POST",
        language="fr-FR",
        speech_timeout="2",
        speech_model="phone_call",
        timeout=5,
        hints=hints_gather,
    )
    gather.say(texte_final, language="fr-FR", voice="Polly.Lea", barge_in=False)
    twiml.say("Merci pour votre appel. À bientôt !", language="fr-FR", voice="Polly.Lea")
    twiml.hangup()

    return str(twiml)

# ====================================================
# ENDPOINT POUR SERVIR LES FICHIERS AUDIO
# ====================================================
@app.get("/audio/{filename}")
def get_audio(filename: str):
    """Retourne le fichier audio MP3."""
    path = f"audio/{filename}"
    if os.path.exists(path):
        return FileResponse(path, media_type="audio/mpeg")
    return {"error": "Fichier non trouvé"}

# ====================================================
# MODE CONSOLE POUR TESTER
# ====================================================
if __name__ == "__main__":
    print("\n" + "="*70)
    print("🎤 AGENT BARBERSHOP OPTIMISÉ — MODE CONSOLE (multi-salon)")
    print("="*70)
    print("Architecture multi-salon — config chargée dynamiquement via get_salon_config()")
    print("\nTape 'quit' pour quitter\n")
    print("="*70 + "\n")

    # Numéro de test — doit correspondre à twilio_number dans la table Salon
    test_phone = "+16066497918"
    _salon_console = get_salon_config(test_phone)
    _session_salon_id = _salon_console.get("id") if _salon_console else None
    _session_nom_salon = _salon_console.get("nom", "") if _salon_console else ""
    _coiffeurs_console = get_coiffeurs(_session_salon_id) if _session_salon_id else []
    _prestations_console = get_prestations(_session_salon_id) if _session_salon_id else []
    if _salon_console:
        print(f"✅ Salon identifié : {_session_nom_salon} (id={_session_salon_id})")
    else:
        print(f"⚠️  Aucun salon trouvé pour {test_phone}")

    _ctx_key_console = f"{test_phone}_{_session_salon_id}" if _session_salon_id else test_phone

    while True:
        user_input = input("👤 Vous: ").strip()

        if user_input.lower() == "quit":
            if session_tokens_total > 0:
                cout_usd, cout_eur = calculer_cout(session_tokens_input, session_tokens_output)
                enregistrer_usage(
                    salon_id=_session_salon_id,
                    salon_nom=_session_nom_salon,
                    twilio_number=test_phone,
                    tokens_input=session_tokens_input,
                    tokens_output=session_tokens_output,
                    nb_echanges=session_nb_echanges,
                    appel_abouti=session_nb_echanges > 0
                )
            print("\n👋 Au revoir!")
            break

        elif user_input.lower() == "cout":
            # Afficher le coût de la session actuelle
            cout_usd, cout_eur = calculer_cout(session_tokens_input, session_tokens_output)
            print(f"\n💰 COÛT SESSION ACTUELLE")
            print(f"   Tokens input  : {session_tokens_input}")
            print(f"   Tokens output : {session_tokens_output}")
            print(f"   Tokens total  : {session_tokens_total}")
            print(f"   Coût USD      : ${cout_usd:.6f}")
            print(f"   Coût EUR      : €{cout_eur:.6f}")
            print(f"   Échanges      : {session_nb_echanges}\n")
            continue

        elif user_input.lower() == "rapport":
            # Afficher le rapport du mois en cours
            rapport_mensuel()
            continue

        elif user_input.lower().startswith("rapport "):
            # Afficher le rapport d'un mois spécifique
            mois = user_input.split(" ", 1)[1].strip()
            rapport_mensuel(mois)
            continue

        if not user_input:
            continue

        response = run_agent(
            user_input, test_phone,
            ctx_key=_ctx_key_console,
            salon=_salon_console or {},
            coiffeurs=_coiffeurs_console,
            prestations=_prestations_console,
        )
        print(f"🤖 Agent: {response}\n")
