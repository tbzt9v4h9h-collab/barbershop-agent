# ====================================================
# AGENT IA COIFFEUR — VERSION GPT-4o OPTIMISÉE
# Avec intégration Supabase + configuration multi-salon
# BUG FIXES : tool calls, rigidité agent, dates relatives
# ====================================================

import os
import unicodedata
import json
from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client as TwilioClient
from apscheduler.schedulers.background import BackgroundScheduler
import openai
import uuid
import re
from datetime import datetime, timedelta, date, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
print("🔵 [BOOT 1/8] load_dotenv OK")

# ====================================================
# ⚙️ CONFIGURATION DU SALON — À PERSONNALISER
# ====================================================
NOM_SALON = "le salon"  # Remplacé au démarrage par Supabase via load_all_salon_data()
TELEPHONE_SALON = "+33939245880"                  # À PERSONNALISER
ADRESSE_SALON = "12 rue Exemple, 75001 Paris"    # À PERSONNALISER

SITE_CLIENT = "https://www.monsite-coiffure.com" # À PERSONNALISER

HORAIRE_OUVERTURE = "09:00"                      # À PERSONNALISER
HORAIRE_FERMETURE = "18:00"                      # À PERSONNALISER
JOURS_OUVERTS = ["mardi", "mercredi", "jeudi", "vendredi", "samedi"] # À PERSONNALISER

COIFFEURS = []  # Chargé depuis Supabase table "employee"

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

# Salon actif pour la session en cours (résolu depuis twilio_number)
_session_salon_id: str | None = None
PRESTATIONS_SALON: list = []  # Liste des prestations chargées depuis Supabase
SALON_DATA_CACHED_AT: datetime | None = None
SALON_CACHE_TTL = 300  # 5 minutes

BASE_URL = "https://barbershop-agent.onrender.com"

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
    mois = datetime.now().strftime("%Y-%m")

    try:
        supabase.table("usage_logs").insert({
            "salon_id": salon_id,
            "salon_nom": salon_nom or NOM_SALON,
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
        mois = datetime.now().strftime("%Y-%m")

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
END_CALL_MESSAGE = "Merci pour votre appel. Bonne journée et à bientôt au salon."

MESSAGE_HORAIRES = f"Le salon est ouvert du mardi au samedi de {HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}."
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
    derniere_activite[telephone] = datetime.now()

def nettoyer_historiques():
    """Supprime les historiques de conversations inactifs depuis plus de 2h."""
    maintenant = datetime.now()
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
    """Alias pour compatibilité — délègue à load_all_salon_data()."""
    load_all_salon_data()

def load_all_salon_data():
    """Charge config salon, coiffeurs et prestations depuis Supabase (avec cache TTL 5 min)."""
    global COIFFEURS, PRESTATIONS_SALON
    global NOM_SALON, TELEPHONE_SALON, ADRESSE_SALON
    global HORAIRE_OUVERTURE, HORAIRE_FERMETURE, JOURS_OUVERTS
    global TWILIO_NUMBER, _session_salon_id, SALON_DATA_CACHED_AT

    maintenant = datetime.now()
    if SALON_DATA_CACHED_AT and \
       (maintenant - SALON_DATA_CACHED_AT).seconds < SALON_CACHE_TTL:
        return  # Données encore fraîches

    if not supabase:
        print("⚠️ [LOAD] Supabase non initialisé")
        return

    try:
        # 1. Charger config salon depuis table "salon"
        salon_result = supabase.table("salon")\
            .select("*")\
            .eq("twilio_number", TWILIO_NUMBER)\
            .limit(1).execute()

        if not salon_result.data:
            print(f"⚠️ [LOAD] Aucun salon pour {TWILIO_NUMBER}")
            return

        s = salon_result.data[0]
        salon_id = s.get("id")
        _session_salon_id = salon_id

        if s.get("nom"):               NOM_SALON = s["nom"]
        if s.get("telephone"):         TELEPHONE_SALON = s["telephone"]
        if s.get("adresse"):           ADRESSE_SALON = s["adresse"]
        if s.get("horaire_ouverture"): HORAIRE_OUVERTURE = s["horaire_ouverture"]
        if s.get("horaire_fermeture"): HORAIRE_FERMETURE = s["horaire_fermeture"]
        if s.get("jours_ouverts"):
            try:
                jours = json.loads(s["jours_ouverts"])
                if isinstance(jours, list) and len(jours) > 0:
                    JOURS_OUVERTS = jours
            except Exception:
                pass

        print(f"✅ [LOAD] Salon : {NOM_SALON} | "
              f"{HORAIRE_OUVERTURE}-{HORAIRE_FERMETURE} | "
              f"Jours : {JOURS_OUVERTS}")

        # 2. Charger les coiffeurs depuis table "employee"
        staff_result = supabase.table("employee")\
            .select("*")\
            .eq("salon_id", salon_id)\
            .execute()

        if staff_result.data:
            COIFFEURS = [
                {
                    "nom": e.get("full_name") or e.get("name") or e.get("first_name", ""),
                    "id": e.get("id"),
                    "specialites": e.get("specialties") or e.get("role", ""),
                }
                for e in staff_result.data
                if e.get("full_name") or e.get("name") or e.get("first_name")
            ]
            print(f"✅ [LOAD] Coiffeurs : {[c['nom'] for c in COIFFEURS]}")
        else:
            COIFFEURS = []
            print(f"⚠️ [LOAD] Aucun coiffeur pour salon_id={salon_id}")

        # 3. Charger les prestations depuis table "service"
        sample_service = supabase.table("service")\
            .select("*").limit(1).execute()
        if sample_service.data:
            print(f"📋 [SERVICE] Colonnes : {list(sample_service.data[0].keys())}")
        else:
            print("📋 [SERVICE] Table vide ou salon_id incorrect")
            all_services = supabase.table("service")\
                .select("*").limit(3).execute()
            print(f"📋 [SERVICE] Sans filtre : {all_services.data}")

        services_result = supabase.table("service")\
            .select("*")\
            .eq("salon_id", salon_id)\
            .execute()

        if services_result.data:
            PRESTATIONS_SALON = services_result.data
            noms = [p.get("name", "") for p in PRESTATIONS_SALON]
            print(f"✅ [LOAD] Prestations : {noms}")
        else:
            PRESTATIONS_SALON = []
            print(f"⚠️ [LOAD] Aucune prestation pour salon_id={salon_id}")

        SALON_DATA_CACHED_AT = datetime.now()

    except Exception as e:
        print(f"❌ [LOAD] Erreur load_all_salon_data : {e}")
        import traceback
        traceback.print_exc()

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
        # Enrichir le contexte avec les RDVs passés
        try:
            rdvs = supabase.table("rendez_vous")\
                .select("*")\
                .eq("client_id", client.get("id"))\
                .order("jour", desc=True).limit(5).execute().data or []
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
                    telephone=None, client_nom=None):
    """Enregistre un RDV dans Supabase, incrémente nb_visites, envoie SMS de confirmation."""
    # RÉSOLUTION SALON_ID EN PRIORITÉ
    if not salon_id:
        try:
            result = supabase.table("salon")\
                .select("id")\
                .eq("twilio_number", TWILIO_NUMBER)\
                .limit(1).execute()
            if result.data:
                salon_id = result.data[0]["id"]
                print(f"✅ [RDV] salon_id={salon_id}")
            else:
                print(f"⚠️ [RDV] Aucun salon pour {TWILIO_NUMBER}")
        except Exception as e:
            print(f"⚠️ [RDV] Erreur salon_id : {e}")

    try:
        heure_fin = ajouter_minutes_hhmm(heure, duree_max)
        row = {
            "client_id":      client_id,
            "salon_id":       salon_id,
            "jour":           jour,
            "heure_debut":    heure,
            "heure_fin":      heure_fin,
            "prestation":     prestation,
            "type_client":    type_client,
            "coupe_detail":   coupe_detail,
            "couleur_detail": couleur_detail,
            "avec_shampoing": avec_shampoing,
            "prix":           prix,
            "statut":         "confirme",
        }
        result = supabase.table("rendez_vous").insert(row).execute()
        rdv_id = result.data[0]["id"] if result.data else None

        # Écriture simultanée dans la table "appointment"
        try:
            salon_id_eff = salon_id or _session_salon_id

            # Résoudre salon_id si toujours None
            if not salon_id_eff:
                res = supabase.table("salon").select("id")\
                    .eq("twilio_number", TWILIO_NUMBER)\
                    .limit(1).execute()
                if res.data:
                    salon_id_eff = res.data[0]["id"]

            print(f"💾 [APPOINTMENT] salon_id={salon_id_eff} "
                  f"client={client_nom} jour={jour} heure={heure}")

            appt_row = {
                "salon_id":     salon_id_eff,
                "client_name":  client_nom or telephone or "Inconnu",
                "client_phone": telephone or "",
                "status":       "confirme",
                "date":         jour,
                "time":         heure + ":00" if len(heure) == 5 else heure,
                "service":      prestation,
                "staff_name":   coupe_detail or "",
                "price":        prix or 0,
                "created_at":   datetime.now(timezone.utc).isoformat(),
            }

            appt_result = supabase.table("appointment")\
                .insert(appt_row).execute()
            appt_id = appt_result.data[0]["id"] \
                      if appt_result.data else None
            print(f"✅ [APPOINTMENT] Inséré id={appt_id}")

        except Exception as e_appt:
            print(f"❌ [APPOINTMENT] ERREUR : {e_appt}")
            import traceback
            traceback.print_exc()

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
            )
        return rdv_id

    except Exception as e:
        print(f"Erreur Supabase enregistrer_rdv: {e}")
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
    """Vérifie la disponibilité d'un créneau dans Supabase."""
    try:
        result = supabase.table("rendez_vous")\
            .select("id")\
            .eq("jour", jour)\
            .eq("heure_debut", heure)\
            .eq("statut", "confirme")\
            .execute()
        return len(result.data) == 0
    except Exception as e:
        print(f"Erreur Supabase est_creneau_disponible: {e}")
        return True

def get_rdv_client(client_id: str) -> list:
    """Récupère les RDV à venir d'un client."""
    try:
        today = datetime.now().date().isoformat()
        result = supabase.table("rendez_vous")\
            .select("*")\
            .eq("client_id", client_id)\
            .eq("statut", "confirme")\
            .gte("jour", today)\
            .order("jour")\
            .execute()
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


def send_sms(to: str, body: str) -> tuple[bool, str | None]:
    """Envoie un SMS via Twilio. Retourne (succès, twilio_sid)."""
    if not twilio_client:
        print("⚠️  [SMS] Client Twilio non initialisé — SMS non envoyé.")
        return False, None
    if to == TWILIO_NUMBER:
        print(f"⚠️  [SMS] Numéro destinataire identique au numéro Twilio ({to}) — ignoré.")
        return False, None
    if not to or not to.startswith("+") or len(to) < 8:
        print(f"⚠️  [SMS] Numéro invalide : {to} — ignoré.")
        return False, None
    try:
        msg = twilio_client.messages.create(body=body, from_=TWILIO_NUMBER, to=to)
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
                          rdv_id: str | None, client_id: str | None):
    """Envoie le SMS de confirmation immédiatement après enregistrement du RDV."""
    prenom = (client_nom or "").split()[0] if client_nom else "vous"
    date_str = _format_date_sms(jour)
    # Heure sans secondes
    heure_str = heure[:5] if heure else heure
    message = (
        f"Bonjour {prenom} ! Votre RDV est confirmé au {NOM_SALON} : "
        f"{prestation} le {date_str} à {heure_str}. "
        f"Pour annuler, appelez le {TELEPHONE_SALON}. À bientôt !"
    )
    ok, sid = send_sms(telephone, message)
    save_rappel_sms(rdv_id, client_id, telephone, message,
                    "envoye" if ok else "echec", twilio_sid=sid)


def send_rappels_sms():
    """
    TÂCHE 2 — Lance les SMS de rappel J-24h.
    Lit les RDV de demain, envoie un SMS à chaque client,
    enregistre le résultat dans rappels_sms.
    Appelée automatiquement à 10h chaque matin par APScheduler.
    """
    demain = (date.today() + timedelta(days=1)).isoformat()
    print(f"📨  [RAPPELS] Envoi des rappels pour le {demain}...")

    try:
        rdvs = supabase.table("rendez_vous")\
            .select("id, client_id, jour, heure_debut, prestation")\
            .eq("jour", demain)\
            .eq("statut", "confirme")\
            .execute().data or []
    except Exception as e:
        print(f"❌  [RAPPELS] Impossible de lire les RDV : {e}")
        return

    if not rdvs:
        print(f"ℹ️   [RAPPELS] Aucun RDV pour demain ({demain}).")
        return

    for rdv in rdvs:
        rdv_id    = rdv.get("id")
        client_id = rdv.get("client_id")
        jour      = rdv.get("jour", demain)
        heure     = (rdv.get("heure_debut") or "")[:5]
        prestation = rdv.get("prestation", "rendez-vous")

        # Récupérer le téléphone du client
        try:
            client_row = supabase.table("clients")\
                .select("telephone, nom")\
                .eq("id", client_id)\
                .limit(1).execute().data
        except Exception as e:
            print(f"⚠️  [RAPPELS] Client {client_id} introuvable : {e}")
            continue

        if not client_row:
            continue

        telephone  = client_row[0].get("telephone", "")
        client_nom = client_row[0].get("nom")

        if not telephone or telephone in ("console_test",):
            print(f"ℹ️   [RAPPELS] Téléphone invalide pour client {client_id}, ignoré.")
            continue

        prenom = (client_nom or "").split()[0] if client_nom else "vous"
        date_str = _format_date_sms(jour)
        message = (
            f"Rappel : Votre RDV au {NOM_SALON} est demain "
            f"{date_str} à {heure} pour {prestation}. "
            f"En cas d'empêchement, appelez le {TELEPHONE_SALON}. À demain !"
        )
        ok, sid = send_sms(telephone, message)
        save_rappel_sms(rdv_id, client_id, telephone, message,
                        "envoye" if ok else "echec", twilio_sid=sid)

    print(f"✅  [RAPPELS] Traitement terminé ({len(rdvs)} RDV).")


def send_rappel_1h(rdv_id, telephone, client_nom, jour, heure, prestation):
    """Envoie un rappel SMS 1h avant le RDV."""
    prenom = (client_nom or "").split()[0] if client_nom else ""
    salut = f"Bonjour {prenom} ! " if prenom else "Bonjour ! "
    message = (f"{salut}Rappel : votre RDV au {NOM_SALON} "
               f"est dans 1 heure à {heure[:5]} pour {prestation}. À tout à l'heure !")
    ok, sid = send_sms(telephone, message)
    save_rappel_sms(rdv_id, None, telephone, message, "envoye" if ok else "echec", twilio_sid=sid)

def check_rappels_1h():
    """Tourne toutes les heures — envoie rappels pour les RDV dans ~1h."""
    if not supabase:
        return
    try:
        maintenant = datetime.now()
        dans_1h = (maintenant + timedelta(hours=1)).strftime("%H:%M")
        aujourdhui = maintenant.date().isoformat()
        rdvs = supabase.table("rendez_vous").select("*")\
            .eq("jour", aujourdhui).eq("statut", "confirme")\
            .eq("heure_debut", dans_1h + ":00").execute().data or []
        for rdv in rdvs:
            client_id = rdv.get("client_id")
            telephone = None
            nom = None
            if client_id and supabase:
                try:
                    c = supabase.table("clients").select("telephone,nom").eq("id", client_id).execute()
                    if c.data:
                        telephone = c.data[0].get("telephone")
                        nom = c.data[0].get("nom")
                except Exception:
                    pass
            if telephone:
                send_rappel_1h(rdv.get("id"), telephone, nom,
                               rdv.get("jour"), rdv.get("heure_debut", dans_1h),
                               rdv.get("prestation", ""))
    except Exception as e:
        print(f"⚠️ check_rappels_1h erreur : {e}")

def send_rapport_hebdo():
    """Envoie un rapport SMS hebdomadaire au salon chaque lundi à 8h."""
    if not supabase or not twilio_client:
        return
    try:
        lundi = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        dimanche = (date.today() - timedelta(days=date.today().weekday() - 6)).isoformat()
        rdvs = supabase.table("rendez_vous").select("*")\
            .gte("jour", lundi).lte("jour", dimanche).eq("statut", "confirme").execute().data or []
        nb_rdv = len(rdvs)
        ca_estime = nb_rdv * 30
        # Nouveaux clients cette semaine
        nouveaux = supabase.table("clients").select("id")\
            .gte("created_at", lundi + "T00:00:00").execute().data or []
        msg = (f"📊 Rapport semaine :\nRDV pris : {nb_rdv}\n"
               f"CA estimé : {ca_estime}€\nNouveaux clients : {len(nouveaux)}\nBonne semaine !")
        twilio_client.messages.create(to=TELEPHONE_SALON, from_=TWILIO_NUMBER, body=msg)
    except Exception as e:
        print(f"⚠️ send_rapport_hebdo erreur : {e}")

# ── Scheduler rappels SMS J-24h (10h00 chaque matin) ──────────────────────
print("🔵 [BOOT 7/8] Démarrage APScheduler…")
try:
    scheduler = BackgroundScheduler(timezone="Europe/Paris")
    scheduler.add_job(send_rappels_sms, "cron", hour=10, minute=0,
                      id="rappels_sms_quotidiens", replace_existing=True)
    scheduler.add_job(check_rappels_1h, "cron", minute=0,
                      id="rappels_1h", replace_existing=True)
    scheduler.add_job(send_rapport_hebdo, "cron", day_of_week="mon", hour=8,
                      id="rapport_hebdo", replace_existing=True)
    scheduler.add_job(nettoyer_historiques, "cron", minute=30,
                      id="nettoyage_historiques", replace_existing=True)
    scheduler.start()
    print("🔵 [BOOT 7/8] Scheduler OK — rappels 10h00, rappels 1h, rapport lundi 8h")
except Exception as _e_sched:
    print(f"⚠️  [BOOT 7/8] Scheduler non démarré : {_e_sched}")

print("🟢 [BOOT 8/8] Module chargé — uvicorn prêt à écouter sur $PORT")

# Vérification des colonnes de la table appointment au démarrage
try:
    sync_appointment_columns()
except Exception as _e_sync:
    print(f"⚠️ [BOOT] sync_appointment_columns : {_e_sync}")

# Chargement complet salon au démarrage
try:
    load_all_salon_data()
except Exception as _e_load:
    print(f"⚠️ [BOOT] load_all_salon_data failed : {_e_load}")

# Vérification colonnes table employee
try:
    _sample_emp = supabase.table("employee").select("*").limit(1).execute()
    if _sample_emp.data:
        print(f"📋 [EMPLOYEE] Colonnes : {list(_sample_emp.data[0].keys())}")
    else:
        print("📋 [EMPLOYEE] Table vide")
except Exception as _e_emp:
    print(f"⚠️ [EMPLOYEE] {_e_emp}")


def annuler_rdv(client_id: str, rdv_id: str) -> bool:
    """Annule un RDV en le marquant comme 'annule'."""
    try:
        supabase.table("rendez_vous")\
            .update({"statut": "annule"})\
            .eq("id", rdv_id)\
            .eq("client_id", client_id)\
            .execute()
        return True
    except Exception as e:
        print(f"Erreur Supabase annuler_rdv: {e}")
        return False

def get_coiffeurs_disponibles(jour: str, heure: str, duree: int = 45) -> list:
    """Retourne la liste des coiffeurs disponibles à l'heure demandée."""
    try:
        rdvs = supabase.table("rendez_vous").select("coiffeur")\
            .eq("jour", jour).eq("heure_debut", heure).eq("statut", "confirme").execute()
        coiffeurs_pris = [r.get("coiffeur") for r in (rdvs.data or [])]
        disponibles = [c for c in COIFFEURS if c["nom"] not in coiffeurs_pris]
        return disponibles if disponibles else COIFFEURS
    except Exception as e:
        print(f"⚠️ Erreur disponibilité coiffeur : {e}")
        return COIFFEURS

def get_prochains_creneaux_disponibles(jour: str, heure_souhaitee: str, nb: int = 3) -> list:
    """Retourne les nb prochains créneaux libres à partir de l'heure souhaitée."""
    creneaux = []
    heure_courante = heure_souhaitee or HORAIRE_OUVERTURE
    for _ in range(20):
        if heure_valide_format(heure_courante) and est_horaire_ouverture(heure_courante):
            if est_creneau_disponible(jour, heure_courante):
                creneaux.append(heure_courante)
                if len(creneaux) >= nb:
                    break
        heure_courante = ajouter_minutes_hhmm(heure_courante, 30)
        if not est_horaire_ouverture(heure_courante):
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
    return datetime.now().date()

def format_date_longue(date_obj):
    return f"{NOMS_JOURS[date_obj.weekday()]} {date_obj.day} {NOMS_MOIS[date_obj.month - 1]}"

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

def est_horaire_ouverture(hhmm):
    if not heure_valide_format(hhmm):
        return False
    valeur = parse_hhmm_en_minutes(hhmm)
    return parse_hhmm_en_minutes(HORAIRE_OUVERTURE) <= valeur <= parse_hhmm_en_minutes(HORAIRE_FERMETURE)

def est_jour_ouvrable(date_iso):
    try:
        d = datetime.strptime(date_iso, "%Y-%m-%d").date()
    except ValueError:
        return False
    return NOMS_JOURS[d.weekday()] in JOURS_OUVERTS

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
    aujourd_hui = datetime.now().date()
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
def build_system_prompt(telephone: str = None) -> str:
    aujourd_hui = datetime.now().date()
    date_str = format_date_longue(aujourd_hui)
    heure_actuelle = datetime.now().strftime("%H:%M")

    ctx = get_client_context(telephone) if telephone else {}
    prenom_client = ctx.get("prenom", "")
    nb_visites = ctx.get("nb_visites", 0)
    humeur_client = ctx.get("humeur", "neutre")
    derniere_prestation = ""
    if ctx.get("derniere_visite"):
        derniere_prestation = ctx["derniere_visite"].get("prestation", "")

    liste_coiffeurs = ", ".join([c["nom"] for c in COIFFEURS]) if COIFFEURS else "notre équipe"
    liste_prestations = ", ".join([p.get("name", "") for p in PRESTATIONS_SALON if p.get("name")]) if PRESTATIONS_SALON else ""

    prompt = f"""Tu es Léa, réceptionniste du salon {NOM_SALON}.
Aujourd'hui : {date_str} à {heure_actuelle}.
Horaires : {HORAIRE_OUVERTURE}-{HORAIRE_FERMETURE}, {', '.join([j.capitalize() for j in JOURS_OUVERTS])}.
Adresse : {ADRESSE_SALON} | Tél : {TELEPHONE_SALON}
Équipe disponible : {liste_coiffeurs}
{f"Prestations : {liste_prestations}" if liste_prestations else ""}

IMPORTANT : Réponds en maximum 1-2 phrases TRÈS courtes. Maximum 20 mots. Direct comme au téléphone.

TON CARACTÈRE :
Tu t'appelles Léa. Chaleureuse, naturelle, professionnelle.
Tu parles comme une vraie Française — jamais comme un robot.
Tu réagis toujours au message avant de poser une question. Tu varies tes expressions.
Tu ne sais pas que tu es une IA. Tu ES Léa.

FAÇON DE PARLER :
- Toujours réagir d'abord, puis questionner :
  ✅ "Avec plaisir ! Et c'est pour quelle prestation ?"
  ❌ "Quelle prestation souhaitez-vous ?"
- Pour le jour : "Vous préférez venir quand ?" / "C'est pour quand à peu près ?" / "Vous avez un jour en tête ?"
- Pour l'heure : "Vers quelle heure ça vous arrangerait ?" / "Plutôt matin ou après-midi ?"
- Pour le prénom : "C'est à quel nom ?" / "Je note à quel nom ?"

EXPRESSIONS À VARIER :
Réactions : Super ! / Très bien ! / Parfait ! / Bonne idée ! / Avec plaisir ! / Bien sûr ! / Pas de souci ! / Génial ! / Oh super ! / Ah nickel ! / Entendu !
Transitions : Et donc... / Du coup... / Alors...
Hésitation naturelle : Voyons voir... / Un instant... / Laissez-moi regarder...

RÈGLES ABSOLUES :
0. Tu réponds TOUJOURS en français. Ne jamais répondre en anglais.
0b. Dès que le client donne son prénom, appelle IMMÉDIATEMENT get_client_info avant de continuer.
1. UNE SEULE question à la fois.
2. Extrais TOUTES les informations disponibles avant de poser des questions.
3. Ordre si manquant : 1) Prestation, 2) Jour/heure, 3) Prénom (toujours en dernier).
4. Tu ne mentionnes JAMAIS que tu es une IA.
5. Si créneau indisponible : propose AUTOMATIQUEMENT le suivant.
6. Ne redemande JAMAIS une info déjà donnée.
7. Maximum 2 phrases par réponse.

FLOW RDV : 1) Extraire prestation+jour+heure → 2) Demander coiffeur (préférence) → 3) Prénom (1 fois) → 4) Shampoing (1 fois) → 5) Récapituler → 6) Enregistrer
Si créneau indisponible : appelle proposer_creneaux, présente 3 options en 1 phrase.
Si "humain" / "parler à quelqu'un" : appelle transfert_humain.

CONFIRMATION RDV :
Dis : "Donc je récapitule : [prestation] [avec/sans shampoing] le [jour] à [heure] avec [coiffeur], c'est bien ça ?"
Attends un "oui" / "c'est ça" / "parfait". Si "non" : demande ce qui change.
Une fois confirmé : "Parfait, c'est réservé ! Vous recevrez un SMS. À [jour] !"

GESTION COIFFEURS :
Étape 1 — Demander naturellement : "Vous avez l'habitude de voir quelqu'un chez nous ?"
Étape 2a — Client cite un nom : appelle verifier_coiffeur_disponible.
  • Dispo : "Super, [Coiffeur] est libre à cette heure-là !"
  • Pas dispo : "Oh, [Coiffeur] est pris là... Il serait libre à [heure1] ou [heure2]. Ou je mets [autre] à l'heure demandée ?"
Étape 2b — Pas de préférence : appelle verifier_coiffeur_disponible, prend le premier dispo.
Si client demande QUI est disponible : cite tous les coiffeurs naturellement.

GESTION PRESTATIONS :
- Enregistre le nom EXACT de la prestation.
- Propose UNIQUEMENT les prestations listées ci-dessus.

AVANT D'APPELER UN OUTIL LENT (verifier_coiffeur_disponible, proposer_creneaux, prendre_rdv) :
Dis TOUJOURS d'abord : "Un instant, je regarde les disponibilités..." OU "Laissez-moi vérifier ça..."
Puis appelle le tool, puis donne le résultat.

CONSEILS :
- Appelle demander_rappel_conseil avec prénom ET numéro.
- Confirme : "Un expert vous rappelle au [numéro] dans les plus brefs délais."

GESTION DES INTERRUPTIONS :
- Si le client change de sujet, adapte-toi immédiatement.
- "Non finalement" / "laisse tomber" → "Pas de souci ! Autre chose ?"
- Ne jamais insister sur un créneau refusé.

ANNULATION RDV :
1. Appelle get_rdv_client_actif. 2. Liste les RDV. 3. Demande confirmation.
4. Appelle annuler_rdv. 5. SMS envoyé automatiquement.

"""

    # Humeur client
    if humeur_client == "pressé":
        prompt += "Client pressé : ultra concis, va droit au but.\n"
    elif humeur_client == "stressé":
        prompt += "Client stressé : sois rassurant, doux, prends le temps.\n"
    elif humeur_client == "joyeux":
        prompt += "Client joyeux : sois enjoué, chaleureux !\n"

    # Client reconnu
    if prenom_client and nb_visites > 0:
        prompt += f"\nCLIENT RECONNU : {prenom_client} ({nb_visites} visite(s))\n"
        if derniere_prestation:
            prompt += f'Dernière prestation : {derniere_prestation}\n'
            prompt += f'Si client répond à l\'accueil, demande : "Vous revenez pour une {derniere_prestation} comme la dernière fois ?"\n'
        else:
            prompt += 'Si client répond à l\'accueil, demande : "Qu\'est-ce que je peux faire pour vous ?"\n'
    elif prenom_client:
        prompt += f"\nCLIENT CONNU : {prenom_client}\n"

    print(f"🧠 [PROMPT] Jours={JOURS_OUVERTS} | Coiffeurs={[c['nom'] for c in COIFFEURS]} | Prestations={len(PRESTATIONS_SALON)} | Humeur={humeur_client}")
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

def process_tool_call(tool_name: str, tool_input: dict, telephone: str) -> str:
    """Exécute une fonction appelée par GPT-4o et retourne le résultat."""

    if tool_name == "prendre_rdv":
        jour = tool_input.get("jour")
        heure = tool_input.get("heure")
        prestation = tool_input.get("prestation", "coupe")
        type_client = tool_input.get("type_client", "homme")
        avec_shampoing = bool(tool_input.get("avec_shampoing", False))
        coiffeur_choisi = tool_input.get("coiffeur")

        # Vérifier que la prestation existe (si liste chargée)
        if PRESTATIONS_SALON:
            prestation_valide = any(
                p.get("name", "").lower() in prestation.lower()
                or prestation.lower() in p.get("name", "").lower()
                for p in PRESTATIONS_SALON
            )
            if not prestation_valide:
                noms = ', '.join(p.get("name", "") for p in PRESTATIONS_SALON)
                return f"Prestation '{prestation}' non disponible. Prestations : {noms}"

        # Vérifier la disponibilité
        if not est_creneau_disponible(jour, heure):
            return f"Créneau indisponible. Merci de vérifier."

        # Récupérer/créer le client
        client = get_or_create_client(telephone)
        client_id  = client.get("id")
        client_nom = client.get("nom")

        # Sauvegarder le nom depuis le contexte session si absent en base
        if not client_nom:
            ctx = get_client_context(telephone)
            client_nom = ctx.get("prenom") or ctx.get("nom")
        nom_fourni = tool_input.get("client_nom") or tool_input.get("prenom")
        if nom_fourni and not client_nom:
            client_nom = nom_fourni
        if client_nom and client_id:
            mettre_a_jour_nom_client(client_id, client_nom)

        # Enregistrer le RDV (déclenche SMS de confirmation)
        enregistrer_rdv(
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
        return f"RDV enregistré pour {jour} à {heure}.{fidelite}"

    elif tool_name == "verifier_disponibilite":
        jour = tool_input.get("jour")
        heure = tool_input.get("heure")
        disponible = est_creneau_disponible(jour, heure)
        return f"Disponibilité : {'libre' if disponible else 'occupé'}"

    elif tool_name == "annuler_rdv":
        client_id = tool_input.get("client_id")
        rdv_id = tool_input.get("rdv_id")

        print(f"🗑️ [ANNULATION] client_id={client_id} rdv_id={rdv_id} tel={telephone}")

        if annuler_rdv(client_id, rdv_id):
            ctx = get_client_context(telephone)
            prenom = ctx.get("prenom") or ctx.get("nom", "").split()[0] or "vous"

            print(f"📱 [ANNULATION SMS] Envoi à {telephone} pour {prenom}")

            message = (
                f"Bonjour {prenom}, votre rendez-vous "
                f"au {NOM_SALON} a bien été annulé. "
                f"Pour reprendre un RDV appelez le "
                f"{TELEPHONE_SALON}. À bientôt !"
            )
            ok, sid = send_sms(telephone, message)
            print(f"📱 [ANNULATION SMS] Résultat : ok={ok} sid={sid}")

            return "RDV annulé avec succès. SMS de confirmation envoyé."
        return "Erreur lors de l'annulation."

    elif tool_name == "get_rdv_client_actif":
        tel = tool_input.get("telephone") or telephone
        client = get_or_create_client(tel)
        client_id = client.get("id")
        print(f"📋 [RDV ACTIF] Recherche RDVs pour client_id={client_id} tel={tel}")
        rdvs = get_rdv_client(client_id)
        if not rdvs:
            return "Aucun RDV à venir pour ce client."
        rdvs_str = []
        for r in rdvs:
            rdvs_str.append(
                f"ID:{r['id']} | {r['jour']} à "
                f"{r['heure_debut']} | {r['prestation']}"
            )
        update_client_context(tel, client_id=client_id)
        return "RDV trouvés : " + " /// ".join(rdvs_str)

    elif tool_name == "get_services":
        services = get_services()
        return f"Services : {', '.join(services)}"

    elif tool_name == "get_client_info":
        client = get_or_create_client(telephone)
        client_id = client.get("id")
        client_nom = client.get("nom", "")
        update_client_context(
            telephone,
            prenom=client_nom.split()[0] if client_nom else None,
            client_id=client_id,
            nom=client_nom or None,
        )
        if client_nom:
            return f"Client trouvé : {client_nom}"
        return "Client nouveau ou sans nom enregistré."

    elif tool_name == "demander_rappel_conseil":
        ctx = get_client_context(telephone)
        prenom = (tool_input.get("nom_client")
                  or ctx.get("prenom")
                  or ctx.get("nom", "").split()[0]
                  or "Client inconnu")
        numero_client = telephone
        message_salon = (f"📞 Rappel conseils : {prenom} au {numero_client} "
                         f"souhaite des conseils. Merci de le rappeler dans les plus brefs délais.")
        ok, _ = send_sms("+33782989198", message_salon)
        if not ok and twilio_client:
            try:
                twilio_client.messages.create(to="+33782989198", from_=TWILIO_NUMBER, body=message_salon)
            except Exception as e:
                print(f"SMS conseil erreur : {e}")
        return (f"Bien sûr ! Un expert de {NOM_SALON} va vous rappeler "
                f"au {numero_client} dans les plus brefs délais. "
                f"Y a-t-il autre chose pour vous ?")

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
                    telephone,
                    prenom=c_nom.split()[0] if c_nom else None,
                    client_id=c.get("id"),
                    nom=c_nom or None,
                )
                return f"Client trouvé par nom : {c_nom} — tél : {c.get('telephone', 'inconnu')}"
            return f"Aucun client nommé '{nom}' trouvé."
        except Exception as e:
            return f"Erreur recherche client : {e}"

    elif tool_name == "verifier_coiffeur_disponible":
        jour = tool_input.get("jour")
        heure = tool_input.get("heure")
        coiffeur_souhaite = tool_input.get("coiffeur_souhaite")
        disponibles = get_coiffeurs_disponibles(jour, heure)
        if coiffeur_souhaite:
            coiffeur_libre = any(c["nom"].lower() == coiffeur_souhaite.lower() for c in disponibles)
            if coiffeur_libre:
                return f"{coiffeur_souhaite} est disponible à {heure}."
            # Trouver prochains créneaux pour ce coiffeur
            creneaux_coiffeur = []
            heure_test = heure
            for _ in range(8):
                heure_test = ajouter_minutes_hhmm(heure_test, 30)
                dispo = get_coiffeurs_disponibles(jour, heure_test)
                if any(c["nom"].lower() == coiffeur_souhaite.lower() for c in dispo):
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
        heure_souhaitee = tool_input.get("heure_souhaitee", HORAIRE_OUVERTURE)
        creneaux = get_prochains_creneaux_disponibles(jour, heure_souhaitee)
        if creneaux:
            return f"Créneaux disponibles le {jour} : {', '.join(creneaux)}."
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
        ctx = get_client_context(telephone)
        nom = ctx.get("prenom") or ctx.get("nom") or telephone
        if twilio_client:
            try:
                twilio_client.messages.create(
                    to=TELEPHONE_SALON,
                    from_=TWILIO_NUMBER,
                    body=f"⚠️ Transfert demandé par {nom} ({telephone}). Raison : {raison}. Rappeler immédiatement.",
                )
            except Exception as e:
                print(f"SMS transfert erreur : {e}")
        return f"Transfert initié. SMS envoyé au salon pour rappeler {nom}."

    return "Fonction inconnue."

# ====================================================
# HELPERS AVANT APPEL GPT
# ====================================================
def get_reponse_cache(message: str) -> str | None:
    """Retourne une réponse immédiate pour les questions fréquentes sans appel GPT."""
    ml = message.lower().strip()
    if any(m in ml for m in ["horaire", "ouvert", "fermé", "quand", "jusqu'à", "à partir"]) \
       or ("heure" in ml and "rendez" not in ml):
        jours = ', '.join([j.capitalize() for j in JOURS_OUVERTS])
        return f"On est ouvert {jours} de {HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}."
    if any(m in ml for m in ["adresse", "situé", "trouver", "localisation", "comment venir"]) \
       or ("où" in ml and len(ml) < 40):
        return f"On est situé au {ADRESSE_SALON}."
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
def run_agent(message_user: str, telephone: str) -> str:
    """
    Exécute l'agent GPT-4o avec function calling (OPTIMISÉ)
    ÉTAPE 2 : Track des tokens OpenAI pour le reporting des coûts
    """
    global session_tokens_input, session_tokens_output, session_tokens_total
    global session_nb_echanges, session_cout_usd, session_cout_eur

    if not client_openai:
        return "⚠️ Erreur: API OpenAI non configurée. Vérifiez votre clé API."

    # Cache réponses fréquentes (évite un appel GPT)
    reponse_cache = get_reponse_cache(message_user)
    if reponse_cache:
        add_to_history(telephone, "assistant", reponse_cache)
        return reponse_cache

    # Ajouter le message utilisateur à l'historique
    add_to_history(telephone, "user", message_user)

    # Détection humeur
    humeur = detecter_humeur(message_user)
    update_client_context(telephone, humeur=humeur)

    # Détection langue anglaise
    mots_anglais = ["hello", "hi", "appointment", "booking", "please", "thank", "yes", "no", "hair", "cut"]
    est_anglais = any(mot in message_user.lower() for mot in mots_anglais)

    # Détecter prénom dans un message court (probablement une réponse de prénom)
    ctx = get_client_context(telephone)
    if not ctx.get("prenom") and 1 <= len(message_user.strip().split()) <= 3:
        prenom_candidat = message_user.strip().split()[0].capitalize()
        if prenom_candidat.isalpha():
            update_client_context(telephone, prenom=prenom_candidat)

    # Construire le system prompt (avec langue si anglais détecté)
    sys_prompt = build_system_prompt(telephone)
    if est_anglais:
        sys_prompt += "\nLe client parle anglais. Réponds en anglais mais garde les données en français dans Supabase."

    # Limiter l'historique à 8 messages pour performance
    history = get_conversation_history(telephone)
    if len(history) > 8:
        history = history[-8:]
        conversation_history[telephone] = history

    # Préparer les messages avec le system prompt en premier
    messages = [{"role": "system", "content": sys_prompt}] + get_conversation_history(telephone)

    # CORRECTION BUG 1 : Nettoyer l'historique des messages orphelins
    messages = clean_messages(messages)

    # Appeler GPT-4o avec function calling
    try:
        response = client_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.85,
            max_tokens=80,
            presence_penalty=0.1,
            frequency_penalty=0.1,
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

    # Si GPT-4o veut appeler une fonction
    if choice.message.tool_calls:
        # Message d'attente pour les outils lents (évite silence Twilio)
        import random as _random
        OUTILS_LENTS = {
            "verifier_coiffeur_disponible", "proposer_creneaux",
            "prendre_rdv", "get_rdv_client_actif", "verifier_disponibilite",
        }
        outil_utilise = choice.message.tool_calls[0].function.name
        if outil_utilise in OUTILS_LENTS:
            MSGS_ATTENTE = [
                "Un instant, je regarde les disponibilités...",
                "Laissez-moi vérifier ça pour vous...",
                "Je jette un œil au planning...",
                "Une seconde, je consulte l'agenda...",
                "Voyons voir ce qu'on a de disponible...",
            ]
            update_client_context(telephone, message_attente=_random.choice(MSGS_ATTENTE))

        # Ajouter le message assistant avec tool_calls
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
        add_assistant_message_with_tools(telephone, content=None, tool_calls=tool_calls_data)

        # Exécuter les appels de fonction et ajouter les résultats
        for tool_call in choice.message.tool_calls:
            tool_name = tool_call.function.name
            tool_input = json.loads(tool_call.function.arguments)
            tool_result = process_tool_call(tool_name, tool_input, telephone)

            # Ajouter le résultat avec le tool_call_id
            add_tool_result(telephone, tool_call.id, tool_result)

        # Relancer GPT-4o avec le résultat des fonctions
        messages = [{"role": "system", "content": sys_prompt}] + get_conversation_history(telephone)
        messages = clean_messages(messages)

        try:
            response = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.85,
                max_tokens=80,
                presence_penalty=0.1,
                frequency_penalty=0.1,
                stream=False,
            )
            # ÉTAPE 2 : Récupérer et accumuler les tokens du deuxième appel
            session_tokens_input += response.usage.prompt_tokens
            session_tokens_output += response.usage.completion_tokens
            session_tokens_total += response.usage.total_tokens
            session_nb_echanges += 1

        except Exception as e:
            print(f"Erreur GPT-4o (retry): {e}")
            history = get_conversation_history(telephone)
            if history and history[-1].get('role') == 'assistant' \
               and history[-1].get('tool_calls'):
                history.pop()
                print("⚠️ [CLEAN] Dernier tool_call retiré après erreur (retry)")
            return "Désolé, pouvez-vous répéter ?"

        choice = response.choices[0]

    # Extraire la réponse texte
    response_text = choice.message.content

    # Ajouter la réponse à l'historique
    add_to_history(telephone, "assistant", response_text)

    # Alerte patron si agent bloqué (pas de RDV après 3+ échanges)
    ctx2 = get_client_context(telephone)
    if not ctx2.get("rdv_pris"):
        nb_echecs = ctx2.get("nb_echecs", 0) + 1
        update_client_context(telephone, nb_echecs=nb_echecs)
        if nb_echecs >= 3 and twilio_client:
            try:
                twilio_client.messages.create(
                    to=TELEPHONE_SALON, from_=TWILIO_NUMBER,
                    body=f"⚠️ L'agent est bloqué avec {telephone}. Rappeler ce client !")
                update_client_context(telephone, nb_echecs=0)
            except Exception:
                pass
    else:
        update_client_context(telephone, nb_echecs=0)

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
    """Retourne le salon_id depuis la table salon via TWILIO_NUMBER."""
    try:
        res = supabase.table("salon").select("id")\
            .eq("twilio_number", TWILIO_NUMBER)\
            .limit(1).execute()
        return res.data[0]["id"] if res.data else None
    except Exception:
        return None

@app.post("/update-config")
async def sync_config(request: Request):
    try:
        data = await request.json()
        print(f"📥 [UPDATE-CONFIG] Payload reçu : {json.dumps(data, indent=2)}")

        global NOM_SALON, TELEPHONE_SALON, ADRESSE_SALON
        global HORAIRE_OUVERTURE, HORAIRE_FERMETURE, JOURS_OUVERTS
        global COIFFEURS, PRESTATIONS_SALON, BASE_URL, TWILIO_NUMBER

        # ── Config salon de base ──────────────────────────────────
        if data.get("salon_name"):
            NOM_SALON = data["salon_name"]
            print(f"✅ [SYNC] NOM_SALON = {NOM_SALON}")
        if data.get("twilio_phone"):
            TELEPHONE_SALON = data["twilio_phone"]
            TWILIO_NUMBER = data["twilio_phone"]
        if data.get("address"):
            ADRESSE_SALON = data["address"]
        if data.get("open_time"):
            HORAIRE_OUVERTURE = data["open_time"]
        if data.get("close_time"):
            HORAIRE_FERMETURE = data["close_time"]
        if data.get("open_days"):
            jours_map = {
                "Lundi": "lundi", "Mardi": "mardi",
                "Mercredi": "mercredi", "Jeudi": "jeudi",
                "Vendredi": "vendredi", "Samedi": "samedi",
                "Dimanche": "dimanche",
            }
            JOURS_OUVERTS = [jours_map.get(j, j.lower()) for j in data["open_days"]]
        if data.get("render_url"):
            BASE_URL = data["render_url"]

        # ── Coiffeurs ─────────────────────────────────────────────
        staff_data = data.get("staff") or data.get("employees") or data.get("coiffeurs")
        if staff_data:
            COIFFEURS = []
            sid = _session_salon_id or salon_id_from_twilio()
            for s in staff_data:
                nom = (s.get("full_name") or s.get("name") or
                       s.get("firstName") or s.get("first_name") or "")
                if nom:
                    COIFFEURS.append({
                        "nom": nom,
                        "id": s.get("id", ""),
                        "specialites": s.get("specialties") or s.get("role", ""),
                    })
                    try:
                        supabase.table("employee").upsert({
                            "id": s.get("id"),
                            "salon_id": sid,
                            "full_name": nom,
                            "specialties": s.get("specialties") or s.get("role", ""),
                        }, on_conflict="id").execute()
                    except Exception as e:
                        print(f"⚠️ [SYNC] Erreur upsert employee : {e}")
            print(f"✅ [SYNC] Coiffeurs : {[c['nom'] for c in COIFFEURS]}")

        # ── Prestations ───────────────────────────────────────────
        services_data = data.get("services") or data.get("prestations")
        if services_data:
            PRESTATIONS_SALON = []
            sid = _session_salon_id or salon_id_from_twilio()
            for sv in services_data:
                nom = sv.get("name") or sv.get("nom") or ""
                if nom:
                    PRESTATIONS_SALON.append(sv)
                    try:
                        supabase.table("service").upsert({
                            "id": sv.get("id"),
                            "salon_id": sid,
                            "name": nom,
                            "price": sv.get("price") or sv.get("prix") or 0,
                            "duration_minutes": sv.get("duration") or sv.get("duree") or 30,
                        }, on_conflict="id").execute()
                    except Exception as e:
                        print(f"⚠️ [SYNC] Erreur upsert service : {e}")
            print(f"✅ [SYNC] Prestations : {[p.get('name') for p in PRESTATIONS_SALON]}")

        print(f"🔄 [UPDATE-CONFIG] JOURS_OUVERTS={JOURS_OUVERTS} | HORAIRE={HORAIRE_OUVERTURE}-{HORAIRE_FERMETURE}")

        # ── Persistance salon dans Supabase ───────────────────────
        if supabase:
            try:
                salon_row = {
                    "twilio_number": TWILIO_NUMBER,
                    "nom": NOM_SALON,
                    "telephone": TELEPHONE_SALON,
                    "adresse": ADRESSE_SALON,
                    "horaire_ouverture": HORAIRE_OUVERTURE,
                    "horaire_fermeture": HORAIRE_FERMETURE,
                    "jours_ouverts": json.dumps(JOURS_OUVERTS),
                }
                print(f"💾 [UPSERT] Sauvegarde salon : {salon_row}")
                supabase.table("salon").upsert(salon_row, on_conflict="twilio_number").execute()
                print(f"💾 [UPSERT] OK")
            except Exception as e_db:
                print(f"⚠️ [SYNC SUPABASE] Erreur persistance salon : {e_db}")

        print(f"✅ [SYNC COMPLÈTE] {NOM_SALON} | {HORAIRE_OUVERTURE}-{HORAIRE_FERMETURE} | Jours: {JOURS_OUVERTS}")
        return {"status": "ok", "salon": NOM_SALON}

    except Exception as e:
        print(f"❌ [SYNC] Erreur : {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/sync-staff")
async def sync_staff(request: Request):
    try:
        data = await request.json()
        print(f"📥 [SYNC-STAFF] Reçu : {data}")

        global COIFFEURS
        staff_list = data.get("staff") or data.get("employees") or []

        COIFFEURS = []
        sid = salon_id_from_twilio()

        for s in staff_list:
            nom = (s.get("full_name") or s.get("name") or
                   s.get("firstName") or "")
            if not nom:
                continue
            COIFFEURS.append({
                "nom": nom,
                "id": s.get("id", ""),
                "specialites": s.get("specialties") or s.get("role", ""),
            })
            try:
                supabase.table("employee").upsert({
                    "id": s.get("id"),
                    "salon_id": sid,
                    "full_name": nom,
                    "specialties": s.get("specialties", ""),
                }, on_conflict="id").execute()
            except Exception as e:
                print(f"⚠️ [SYNC-STAFF] Erreur : {e}")

        print(f"✅ [SYNC-STAFF] {len(COIFFEURS)} coiffeurs : "
              f"{[c['nom'] for c in COIFFEURS]}")
        return {"status": "ok", "coiffeurs": len(COIFFEURS)}
    except Exception as e:
        print(f"❌ [SYNC-STAFF] {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/sync-services")
async def sync_services(request: Request):
    try:
        data = await request.json()
        print(f"📥 [SYNC-SERVICES] Reçu : {data}")

        global PRESTATIONS_SALON
        services_list = data.get("services") or data.get("prestations") or []

        PRESTATIONS_SALON = []
        sid = salon_id_from_twilio()

        for sv in services_list:
            nom = sv.get("name") or sv.get("nom") or ""
            if not nom:
                continue
            PRESTATIONS_SALON.append(sv)
            try:
                supabase.table("service").upsert({
                    "id": sv.get("id"),
                    "salon_id": sid,
                    "name": nom,
                    "price": sv.get("price") or 0,
                    "duration_minutes": sv.get("duration") or 30,
                }, on_conflict="id").execute()
            except Exception as e:
                print(f"⚠️ [SYNC-SERVICES] Erreur : {e}")

        print(f"✅ [SYNC-SERVICES] {len(PRESTATIONS_SALON)} prestations")
        return {"status": "ok", "prestations": len(PRESTATIONS_SALON)}
    except Exception as e:
        print(f"❌ [SYNC-SERVICES] {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ====================================================
# ENDPOINT PRINCIPAL
# ====================================================
@app.post("/appel", response_class=PlainTextResponse)
def handle_appel(
    From: str = Form(default=""),
    Called: str = Form(default=""),
    SpeechResult: str = Form(default=""),
):
    global NOM_SALON, TELEPHONE_SALON, ADRESSE_SALON
    global HORAIRE_OUVERTURE, HORAIRE_FERMETURE, JOURS_OUVERTS, TWILIO_NUMBER

    twiml = VoiceResponse()

    # Charger config, coiffeurs et prestations depuis Supabase
    load_all_salon_data()
    print(f"📞 [APPEL] NOM_SALON={NOM_SALON} | JOURS={JOURS_OUVERTS} | HORAIRES={HORAIRE_OUVERTURE}-{HORAIRE_FERMETURE}")

    # Anti-spam : bloquer si +10 appels en 24h
    def est_spam(tel: str) -> bool:
        if not supabase or not tel:
            return False
        try:
            hier = (datetime.now() - timedelta(days=1)).isoformat()
            result = supabase.table("usage_logs").select("id")\
                .eq("twilio_number", tel).gte("created_at", hier).execute()
            return len(result.data or []) > 10
        except Exception:
            return False

    telephone_appelant = From or Called

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

    HINTS = (
        "rendez-vous, coupe, couleur, brushing, shampoing, annuler, demain, "
        "lundi, mardi, mercredi, jeudi, vendredi, samedi, bonjour, oui, non, "
        "merci, au revoir, barbe, dégradé, soin, balayage, mèches, prénom, heure"
    )

    if not SpeechResult:
        # Compteur de silences par session
        silence_key = f"silence_{telephone_appelant}"
        nb_silences = client_context.get(silence_key, 0)

        if nb_silences >= 2:
            twiml.say(
                "Je ne vous entends pas. N'hésitez pas à nous rappeler. À bientôt !",
                language="fr-FR", voice="Polly.Lea",
            )
            twiml.hangup()
            client_context.pop(silence_key, None)
            return str(twiml)

        if nb_silences == 1:
            client_context[silence_key] = nb_silences + 1
            gather = twiml.gather(
                input="speech", action="/appel", method="POST",
                language="fr-FR", speech_timeout="1",
                speech_model="phone_call", timeout=6, hints=HINTS,
            )
            gather.say("Vous êtes toujours là ? Je vous écoute.", language="fr-FR", voice="Polly.Lea")
            return str(twiml)

        client_context[silence_key] = nb_silences + 1
        ctx_accueil = get_client_context(telephone_appelant)
        prenom_connu = ctx_accueil.get("prenom", "")
        nb_visites_connu = ctx_accueil.get("nb_visites", 0)
        import random as _rand
        if prenom_connu and nb_visites_connu > 0:
            accueils = [
                f"Bonjour {prenom_connu} ! Ça fait plaisir de vous retrouver ! Comment vous allez ?",
                f"Ah, bonjour {prenom_connu} ! Ravi de vous réentendre ! Vous allez bien ?",
                f"Bonjour {prenom_connu} ! Toujours un plaisir ! Comment ça va ?",
                f"Oh, bonjour {prenom_connu} ! Content de vous réentendre ! Tout va bien ?",
            ]
            message_accueil = _rand.choice(accueils)
        else:
            accueils = [
                f"Bonjour et bienvenue chez {NOM_SALON}, c'est Léa à l'appareil, comment puis-je vous aider ?",
                f"{NOM_SALON} bonjour, Léa à l'écoute, qu'est-ce que je peux faire pour vous ?",
                f"Bonjour ! Vous êtes bien chez {NOM_SALON}, Léa à votre service, que puis-je faire pour vous ?",
            ]
            message_accueil = _rand.choice(accueils)
        gather = twiml.gather(
            input="speech", action="/appel", method="POST",
            language="fr-FR", speech_timeout="1",
            speech_model="phone_call", timeout=6, hints=HINTS,
        )
        gather.say(message_accueil, language="fr-FR", voice="Polly.Lea")
        return str(twiml)

    telephone = telephone_appelant
    response_text = run_agent(SpeechResult, telephone)

    PHRASES_FIN = [
        "au revoir", "bonne journée", "à bientôt", "c'est tout",
        "ça sera tout", "bye", "bonne continuation", "à la prochaine",
        "ciao", "merci beaucoup", "super merci", "ok merci",
        "rdv est confirmé", "c'est réservé",
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

    est_fin_client = any(p in SpeechResult.lower() for p in PHRASES_FIN)
    est_fin_agent = any(p in (response_text or "").lower() for p in PHRASES_FIN)

    if est_fin_client or est_fin_agent:
        reponse_fin = _rand2.choice(REPONSES_FIN)
        twiml.say(reponse_fin, language="fr-FR", voice="Polly.Lea")
        twiml.hangup()
        return str(twiml)

    # Message d'attente pré-outil (évite silence Twilio)
    ctx_post = get_client_context(telephone)
    msg_attente = ctx_post.pop("message_attente", None)
    if msg_attente:
        update_client_context(telephone)  # flush (pop already done on dict)
    texte_final = (msg_attente + " " + response_text) if msg_attente else response_text

    gather = twiml.gather(
        input="speech",
        action="/appel",
        method="POST",
        language="fr-FR",
        speech_timeout="1",
        speech_model="phone_call",
        timeout=6,
        hints=HINTS,
    )
    gather.say(texte_final, language="fr-FR", voice="Polly.Lea")
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
    print("🎤 AGENT BARBERSHOP OPTIMISÉ — MODE CONSOLE")
    print("="*70)
    print(f"Salon: {NOM_SALON}")
    print(f"Horaires: {HORAIRE_OUVERTURE} - {HORAIRE_FERMETURE}")
    print(f"Jours: {', '.join(JOURS_OUVERTS)}")
    print("\nTape 'quit' pour quitter\n")
    print("="*70 + "\n")

    # Numéro de test — doit correspondre à twilio_number dans la table Salon
    test_phone = "+16066497918"
    # Note: _session_salon_id est défini au niveau du module
    try:
        salon = get_salon_by_twilio(test_phone)
    except Exception as e:
        print(f"⚠️  Erreur get_salon_by_twilio: {e}")
        salon = None
    if salon:
        _session_salon_id = salon.get("id")
        print(f"✅ Salon identifié : {salon.get('nom', salon.get('name', _session_salon_id))} (id={_session_salon_id})")
    else:
        _session_salon_id = None
        print(f"⚠️  Aucun salon trouvé pour {test_phone} — le salon_id ne sera pas enregistré dans les RDV.")

    while True:
        user_input = input("👤 Vous: ").strip()

        # ÉTAPE 6 : Commandes spéciales pour le tracking
        if user_input.lower() == "quit":
            # Enregistrer l'usage avant de quitter
            if session_tokens_total > 0:
                cout_usd, cout_eur = calculer_cout(session_tokens_input, session_tokens_output)
                enregistrer_usage(
                    salon_id=_session_salon_id,
                    salon_nom=NOM_SALON,
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

        # Exécuter l'agent
        response = run_agent(user_input, test_phone)
        print(f"🤖 Agent: {response}\n")
