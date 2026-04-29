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
NOM_SALON = "Chez les fdp du dégradé"          # À PERSONNALISER
TELEPHONE_SALON = "+33939245880"                  # À PERSONNALISER
ADRESSE_SALON = "12 rue Exemple, 75001 Paris"    # À PERSONNALISER

SITE_CLIENT = "https://www.monsite-coiffure.com" # À PERSONNALISER

HORAIRE_OUVERTURE = "09:00"                      # À PERSONNALISER
HORAIRE_FERMETURE = "18:00"                      # À PERSONNALISER
JOURS_OUVERTS = ["mardi", "mercredi", "jeudi", "vendredi", "samedi"] # À PERSONNALISER

COIFFEURS = [                                    # À PERSONNALISER
    {"nom": "Sophie", "specialites": "coupe femme, couleur"},
    {"nom": "Marc",   "specialites": "coupe homme, barbe"},
]

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

def get_conversation_history(telephone: str):
    """Récupère l'historique de conversation pour ce numéro."""
    if telephone not in conversation_history:
        conversation_history[telephone] = []
    return conversation_history[telephone]

def add_to_history(telephone: str, role: str, content: str):
    """Ajoute un message à l'historique."""
    history = get_conversation_history(telephone)
    history.append({"role": role, "content": content})

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
    CORRECTION BUG 1 : Nettoie l'historique des messages orphelins
    Supprime les messages 'tool' qui ne sont pas précédés
    d'un message 'assistant' avec 'tool_calls'.
    """
    cleaned = []
    for i, msg in enumerate(messages):
        if msg.get('role') == 'tool':
            # Vérifier que le message précédent est un assistant avec tool_calls
            if cleaned and cleaned[-1].get('role') == 'assistant' and cleaned[-1].get('tool_calls'):
                cleaned.append(msg)
            # Sinon, ignorer ce message orphelin
        else:
            cleaned.append(msg)
    return cleaned

# ====================================================
# SUPABASE — FONCTIONS CLIENT & RDV
# ====================================================

def load_salon_data(twilio_number: str = None):
    """Charge depuis Supabase l'équipe et les prestations du salon."""
    global COIFFEURS, PRESTATIONS_SALON
    if not supabase or not _session_salon_id:
        return
    try:
        staff = supabase.table("employee").select("*")\
            .eq("salon_id", _session_salon_id).eq("is_active", True).execute()
        if staff.data:
            COIFFEURS = [{"nom": e.get("name"), "id": e.get("id"),
                          "specialites": e.get("specialties", "")} for e in staff.data]
            print(f"✅ [DATA] {len(COIFFEURS)} coiffeurs chargés")
    except Exception as e:
        print(f"⚠️ [DATA] Erreur chargement équipe : {e}")
    try:
        services = supabase.table("service").select("*")\
            .eq("salon_id", _session_salon_id).execute()
        if services.data:
            PRESTATIONS_SALON = services.data
            print(f"✅ [DATA] {len(PRESTATIONS_SALON)} prestations chargées")
    except Exception as e:
        print(f"⚠️ [DATA] Erreur chargement prestations : {e}")

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
            update_client_context(telephone,
                nb_visites=client.get("nb_visites", 0),
                derniere_visite=rdvs[0] if rdvs else None)
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
    try:
        heure_fin = ajouter_minutes_hhmm(heure, duree_max)
        row = {
            "client_id":      client_id,
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
            appt_row = {
                "client_name":  client_nom or telephone or "Inconnu",
                "client_phone": telephone or "",
                "status":       "confirme",
                "date":         jour,
                "time":         heure + ":00" if len(heure) == 5 else heure,
                "created_at":   datetime.now(timezone.utc).isoformat(),
            }
            if salon_id_eff:
                appt_row["salon_id"] = salon_id_eff
            appt_result = supabase.table("appointment").insert(appt_row).execute()
            appt_id = appt_result.data[0]["id"] if appt_result.data else None
            print(f"✅ [appointment] Ligne créée — id={appt_id}")
        except Exception as e_appt:
            print(f"⚠️  [appointment] Erreur insert : {e_appt}")

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
    scheduler.start()
    print("🔵 [BOOT 7/8] Scheduler OK — rappels 10h00, rappels 1h, rapport lundi 8h")
except Exception as _e_sched:
    print(f"⚠️  [BOOT 7/8] Scheduler non démarré : {_e_sched}")

print("🟢 [BOOT 8/8] Module chargé — uvicorn prêt à écouter sur $PORT")


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
    """
    OPTIMISATION 1 & 3 & 5 : Prompt système amélioré
    - Inclus les règles absolues
    - Gestion intelligente des infos
    - Confirmation avant enregistrement
    - Mémoire du client
    """
    aujourd_hui = datetime.now().date()
    date_str = format_date_longue(aujourd_hui)

    prompt = f"""Tu es une réceptionniste vocale professionnelle et chaleureuse du salon "{NOM_SALON}".

📅 Nous sommes le {date_str}.
🏢 Salon : {NOM_SALON}
📞 Téléphone : {TELEPHONE_SALON}
📍 Adresse : {ADRESSE_SALON}
⏰ Horaires : {HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}
📅 Jours ouverts : {', '.join(JOURS_OUVERTS)}
🕸️ Site : {SITE_CLIENT}

RÈGLES ABSOLUES :
0. Tu réponds TOUJOURS en français, peu importe la langue du message reçu. Ne jamais répondre en anglais.
0b. Dès que le client donne son prénom, appelle IMMÉDIATEMENT get_client_info avec son numéro de téléphone pour enregistrer le contexte, avant même de continuer la conversation.
1. Tu poses UNE SEULE question à la fois (très important)
2. Tu extrais TOUTES les informations disponibles dans le message du client AVANT de poser des questions
3. Si le client dit "coupe homme pour demain vers 14h", tu ne redemandes RIEN de tout ça
4. Ordre des questions si manquantes : 1) Prestation, 2) Jour/heure, 3) Prénom (toujours en dernier)
5. Tu acceptes les corrections sans te perdre
6. Tu utilises le prénom du client dès qu'il te l'a donné
7. Tu ne mentionnes JAMAIS que tu es une IA
8. Tu ne raccroches que si le RDV est confirmé OU si le client dit au revoir explicitement
9. Si un créneau n'est pas disponible, tu proposes AUTOMATIQUEMENT le suivant disponible
10. Si le salon est fermé ce jour, tu dis pourquoi et tu proposes un autre jour
11. Réponds en maximum 1-2 phrases courtes. Sois direct et concis. Pas de phrases longues.
12. Dès que le client donne son prénom, appelle immédiatement get_client_info pour vérifier s'il existe déjà, puis utilise rechercher_client_par_nom si aucun nom n'est trouvé par téléphone.
13. Si le client demande des conseils (coloration, coupe conseillée, soin, entretien, etc.), appelle immédiatement demander_rappel_conseil puis dis au client : "Bien sûr [prénom] ! Un expert vous rappelle au [numéro] dans les plus brefs délais."
14. Quand le client donne son prénom, répète-le UNE SEULE FOIS pour confirmer ("Parfait [Prénom] !") puis CONTINUE IMMÉDIATEMENT avec la suite. Ne redemande JAMAIS le prénom si tu l'as déjà reçu dans cette conversation. Si tu as déjà le prénom dans l'historique, utilise-le directement sans le redemander.
15. Ne redemande JAMAIS une information déjà donnée dans la conversation.
16. Si le client répète son prénom, dis simplement "Oui je vous ai bien noté [Prénom]" et continue.
17. Maximum 2 phrases par réponse, toujours.

FLOW RDV : 1) Extraire prestation+jour+heure → 2) Demander coiffeur (préférence) → 3) Demander prénom (1 fois) → 4) Demander shampoing (1 fois) → 5) Récapituler et confirmer → 6) Enregistrer → "RDV confirmé !"
Si créneau indisponible : appelle proposer_creneaux, présente les 3 options en 1 phrase.
Si demande de prix : calcule total et propose un créneau dans la même phrase. Donne le prix AVANT de confirmer.
Si "parler à quelqu'un" / "humain" : appelle transfert_humain.
Si événement urgent (mariage, cérémonie) : priorité créneaux du jour, appelle transfert_humain.
Après coupe homme : propose barbe (1 fois). Après coupe femme : propose soin (1 fois).

GESTION COIFFEURS :
- Toujours demander préférence coiffeur après prestation/heure
- Appelle verifier_coiffeur_disponible pour confirmer la disponibilité
- Si coiffeur non dispo : propose heure alternative OU autre coiffeur
- Si pas de préférence : attribue le premier disponible

GESTION PRESTATIONS :
- Enregistre le nom EXACT de la prestation comme demandé par le client
- Si prestation inconnue : explique et propose de lister les prestations disponibles

CONSEILS :
- Quand client demande conseil, utilise son prénom connu ou demande-le
- Appelle demander_rappel_conseil avec prénom ET numéro appelant
- Confirme : "Un expert vous rappelle au [numéro] dans les plus brefs délais"

MÉMOIRE DU CLIENT :
"""

    # Ajouter les infos du client si connu
    if telephone:
        ctx = get_client_context(telephone)
        if ctx.get("nom") or ctx.get("prenom"):
            prenom = ctx.get("prenom") or ctx.get("nom", "").split()[0]
            prompt += f"- Ce client s'appelle {prenom}\n"
            rdvs = get_rdv_client(ctx.get("client_id")) if ctx.get("client_id") else []
            if rdvs:
                dernier_rdv = rdvs[-1]
                derniere_prestation = dernier_rdv.get("prestation", "")
                derniere_date = dernier_rdv.get("jour", "")
                prompt += f"- Dernière visite : {derniere_date} pour {derniere_prestation}\n"
                prompt += (
                    f"Accueille-le : 'Bonjour {prenom} ! Comment allez-vous depuis la dernière fois ? "
                    f"Je vois que vous étiez venu(e) pour {derniere_prestation} le {derniere_date}. "
                    f"Souhaitez-vous reprendre la même chose ?'\n"
                )
            else:
                prompt += f"Accueille-le chaleureusement par son prénom.\n"

    # Ajouter les prestations chargées depuis Supabase
    if PRESTATIONS_SALON:
        noms_prestations = [p.get("name", "") for p in PRESTATIONS_SALON if p.get("name")]
        prompt += f"\nPRESTATIONS DISPONIBLES : {', '.join(noms_prestations)}\n"
        prompt += ("Si client demande une prestation non listée : "
                   "explique qu'elle n'est pas disponible et propose de lister par genre.\n")

    # Ajouter les coiffeurs disponibles
    if COIFFEURS:
        noms_coiffeurs = [c.get("nom", "") for c in COIFFEURS if c.get("nom")]
        prompt += f"ÉQUIPE : {', '.join(noms_coiffeurs)}\n"

    print(f"🧠 [PROMPT] Jours dans prompt : {JOURS_OUVERTS}")
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
        if annuler_rdv(client_id, rdv_id):
            client = get_or_create_client(telephone)
            prenom = (client.get("nom") or "").split()[0] or "vous"
            message = (f"Bonjour {prenom}, votre rendez-vous au {NOM_SALON} a bien été annulé. "
                       f"Pour reprendre un RDV appelez le {TELEPHONE_SALON}. À bientôt !")
            send_sms(telephone, message)
            return "RDV annulé avec succès."
        return "Erreur lors de l'annulation."

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
    CACHE_REPONSES = {
        "horaires": f"Nous sommes ouverts de {HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}.",
        "adresse":  f"Nous sommes situés au {ADRESSE_SALON}.",
        "prix":     "Les tarifs commencent à 15€ pour une coupe homme.",
    }
    message_lower = message_user.lower()
    if "horaire" in message_lower or ("heure" in message_lower and "rendez" not in message_lower):
        return CACHE_REPONSES["horaires"]
    if "adresse" in message_lower or ("où" in message_lower and "situé" in message_lower):
        return CACHE_REPONSES["adresse"]
    if "prix" in message_lower or "tarif" in message_lower or "coût" in message_lower or "combien" in message_lower:
        return CACHE_REPONSES["prix"]

    # Ajouter le message utilisateur à l'historique
    add_to_history(telephone, "user", message_user)

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
            temperature=0.3,
            max_tokens=150,
        )
        # ÉTAPE 2 : Récupérer et accumuler les tokens
        session_tokens_input += response.usage.prompt_tokens
        session_tokens_output += response.usage.completion_tokens
        session_tokens_total += response.usage.total_tokens
        session_nb_echanges += 1

    except Exception as e:
        print(f"Erreur GPT-4o: {e}")
        return "Désolé, une erreur s'est produite. Pouvez-vous répéter?"

    # Traiter la réponse
    choice = response.choices[0]

    # Si GPT-4o veut appeler une fonction
    if choice.message.tool_calls:
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
                temperature=0.3,
                max_tokens=150,
            )
            # ÉTAPE 2 : Récupérer et accumuler les tokens du deuxième appel
            session_tokens_input += response.usage.prompt_tokens
            session_tokens_output += response.usage.completion_tokens
            session_tokens_total += response.usage.total_tokens
            session_nb_echanges += 1

        except Exception as e:
            print(f"Erreur GPT-4o (retry): {e}")
            return "Désolé, une erreur s'est produite. Pouvez-vous répéter?"

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

@app.post("/update-config")
async def sync_config(request: Request):
    try:
        data = await request.json()

        global NOM_SALON, TELEPHONE_SALON, ADRESSE_SALON
        global HORAIRE_OUVERTURE, HORAIRE_FERMETURE, JOURS_OUVERTS
        global COIFFEURS, BASE_URL, TWILIO_NUMBER

        if data.get("salon_name"):
            NOM_SALON = data["salon_name"]
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
        if data.get("staff"):
            COIFFEURS = [{"nom": c.get("name"), "specialites": c.get("specialties", "")}
                         for c in data["staff"]]
        if data.get("render_url"):
            BASE_URL = data["render_url"]

        print(f"🔄 [UPDATE-CONFIG] Reçu : {data}")
        print(f"🔄 [UPDATE-CONFIG] JOURS_OUVERTS après update : {JOURS_OUVERTS}")
        print(f"🔄 [UPDATE-CONFIG] HORAIRE : {HORAIRE_OUVERTURE}-{HORAIRE_FERMETURE}")

        # Persistance dans Supabase
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
                print(f"💾 [UPSERT] Sauvegarde : {salon_row}")
                supabase.table("salon").upsert(salon_row, on_conflict="twilio_number").execute()
                print(f"💾 [UPSERT] OK")
            except Exception as e_db:
                print(f"⚠️ [SYNC SUPABASE] Erreur persistance : {e_db}")

        print(f"✅ [SYNC COMPLÈTE] {NOM_SALON} | {HORAIRE_OUVERTURE}-{HORAIRE_FERMETURE} | Jours: {JOURS_OUVERTS}")
        return {"status": "ok", "salon": NOM_SALON}

    except Exception as e:
        print(f"❌ [SYNC] Erreur : {e}")
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

    # Charger la config salon depuis Supabase (persistance entre redémarrages)
    try:
        called = Called or TWILIO_NUMBER
        salon_data = supabase.table("salon").select("*")\
            .eq("twilio_number", called).limit(1).execute()
        if salon_data.data:
            s = salon_data.data[0]
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
                        print(f"✅ [APPEL] Jours chargés : {JOURS_OUVERTS}")
                except Exception as e_j:
                    print(f"⚠️ [APPEL] Erreur parse jours : {e_j}")
        print(f"📞 [APPEL] NOM_SALON={NOM_SALON}")
        print(f"📞 [APPEL] JOURS_OUVERTS={JOURS_OUVERTS}")
        print(f"📞 [APPEL] HORAIRES={HORAIRE_OUVERTURE}-{HORAIRE_FERMETURE}")
    except Exception as e:
        print(f"⚠️ [APPEL] Erreur chargement config : {e}")

    # Charger équipe et prestations depuis Supabase
    load_salon_data()

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
    if est_spam(telephone_appelant):
        twiml.say("Ce numéro a été temporairement suspendu.", language="fr-FR", voice="Polly.Lea")
        twiml.hangup()
        return str(twiml)

    if not SpeechResult:
        gather = twiml.gather(
            input="speech",
            action="/appel",
            method="POST",
            language="fr-FR",
            speech_timeout="auto",
            speech_model="phone_call",
            timeout=5,
        )
        gather.say(
            f"Bonjour et bienvenue chez {NOM_SALON}, comment puis-je vous aider ?",
            language="fr-FR",
            voice="Polly.Lea",
        )
        return str(twiml)

    telephone = telephone_appelant
    response_text = run_agent(SpeechResult, telephone)

    phrases_fin = [
        "bonne journée", "à bientôt", "au revoir",
        "merci pour votre appel", "à très bientôt",
        "bonne continuation", "à la prochaine",
        "rdv est confirmé", "rendez-vous est confirmé",
    ]
    est_fin = any(p in (response_text or "").lower() for p in phrases_fin)

    if est_fin:
        gather = twiml.gather(
            input="speech",
            action="/appel",
            method="POST",
            language="fr-FR",
            speech_timeout="auto",
            speech_model="phone_call",
            timeout=3,
        )
        gather.say(response_text, language="fr-FR", voice="Polly.Lea")
        twiml.hangup()
    else:
        gather = twiml.gather(
            input="speech",
            action="/appel",
            method="POST",
            language="fr-FR",
            speech_timeout="auto",
            speech_model="phone_call",
            timeout=8,
        )
        gather.say(response_text, language="fr-FR", voice="Polly.Lea")
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
