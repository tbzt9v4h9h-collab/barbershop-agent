# ====================================================
# AGENT IA COIFFEUR — VERSION GPT-4o AVEC FUNCTION CALLING
# Avec intégration Supabase + configuration multi-salon
# ====================================================

import os
import unicodedata
import json
from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse
import openai
import uuid
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.py")
load_dotenv(dotenv_path=dotenv_path)

# ====================================================
# ⚙️ CONFIGURATION DU SALON — À PERSONNALISER
# ====================================================
NOM_SALON = "Chez les fdp du dégradé"          # À PERSONNALISER
TELEPHONE_SALON = "01 23 45 67 89"               # À PERSONNALISER
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

openai.api_key = os.getenv("API_KEY")
try:
    client_openai = openai.OpenAI(api_key=openai.api_key)
except Exception as e:
    print(f"⚠️  Erreur initialisation OpenAI: {e}")
    client_openai = None

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BASE_URL = "https://concealingly-highly-felica.ngrok-free.dev"

app = FastAPI()
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
conversation_history = {}  # {phone: [{"role": "user/assistant", "content": "..."}]}
client_context = {}        # {phone: {"nom": "...", "client_id": "..."}}

def get_conversation_history(telephone: str):
    """Récupère l'historique de conversation pour ce numéro."""
    if telephone not in conversation_history:
        conversation_history[telephone] = []
    return conversation_history[telephone]

def add_to_history(telephone: str, role: str, content: str):
    """Ajoute un message à l'historique."""
    history = get_conversation_history(telephone)
    history.append({"role": role, "content": content})

def get_client_context(telephone: str):
    """Récupère le contexte client (nom, ID, etc)."""
    if telephone not in client_context:
        client_context[telephone] = {}
    return client_context[telephone]

def update_client_context(telephone: str, **kwargs):
    """Met à jour le contexte client."""
    ctx = get_client_context(telephone)
    ctx.update(kwargs)

# ====================================================
# SUPABASE — FONCTIONS CLIENT & RDV
# ====================================================

def get_or_create_client(telephone: str) -> dict:
    """Cherche le client par son numéro. S'il existe → retourne sa fiche. S'il n'existe pas → crée une fiche vide."""
    try:
        result = supabase.table("clients")\
            .select("*")\
            .eq("telephone", telephone)\
            .execute()
        if result.data:
            return result.data[0]
        nouveau = supabase.table("clients")\
            .insert({"telephone": telephone})\
            .execute()
        return nouveau.data[0]
    except Exception as e:
        print(f"Erreur Supabase get_or_create_client: {e}")
        return {"id": None, "telephone": telephone, "nom": None, "nb_visites": 0}

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
                    duree_max, prix, avec_shampoing=False):
    """Enregistre un RDV dans Supabase et incrémente le compteur de visites."""
    try:
        heure_fin = ajouter_minutes_hhmm(heure, duree_max)
        supabase.table("rendez_vous").insert({
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
        }).execute()

        if client_id:
            client = supabase.table("clients")\
                .select("nb_visites")\
                .eq("id", client_id)\
                .execute().data
            if client:
                supabase.table("clients").update({
                    "nb_visites": client[0]["nb_visites"] + 1
                }).eq("id", client_id).execute()
    except Exception as e:
        print(f"Erreur Supabase enregistrer_rdv: {e}")

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
        result = client_openai.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=message,
        )
        audio_bytes = result.read() if hasattr(result, "read") else bytes(result)
        f.write(audio_bytes)
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
    return f"{NOMS_JOURS[date_obj.weekday()]} {date_obj.day} {NOMS_MOIS[date_obj.month - 1]} {date_obj.year}"

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
    return d.weekday() in [1, 2, 3, 4, 5]  # mardi(1) à samedi(5)

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
# PROMPT SYSTÈME POUR GPT-4o
# ====================================================
def build_system_prompt():
    """Construit le prompt système pour GPT-4o."""
    return f"""Tu es une réceptionniste vocale professionnelle du salon "{NOM_SALON}".

RÈGLES DE COMPORTEMENT:
1. Tu parles uniquement en français, de façon naturelle et chaleureuse
2. Tu poses UNE SEULE question à la fois
3. Tu retiens le prénom du client et l'utilises dès le 2ème message
4. Tu comprends les expressions naturelles : "vers 15h", "demain matin", "mardi prochain"
5. Si le client dit "pour un homme une coupe" en une phrase, tu comprends tout et tu ne redemandes pas "homme ou femme?"
6. Tu ne raccroches que quand le RDV est confirmé ou si le client dit au revoir
7. Tu gères les cas : RDV complet (propose autre créneau), salon fermé, hors horaires
8. Tu ne mentionnes jamais que tu es une IA sauf si on te le demande directement

INFORMATIONS DU SALON:
- Nom: {NOM_SALON}
- Téléphone: {TELEPHONE_SALON}
- Adresse: {ADRESSE_SALON}
- Site: {SITE_CLIENT}
- Horaires: {HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}
- Jours ouverts: {', '.join(JOURS_OUVERTS)}

TÂCHES POSSIBLES (utilise les fonctions disponibles):
1. Prendre un rendez-vous → appelle prendre_rdv()
2. Vérifier la disponibilité → appelle verifier_disponibilite()
3. Annuler un RDV → appelle annuler_rdv()
4. Donner les services → appelle get_services()
5. Récupérer info client → appelle get_client_info()

Sois toujours professionnel, courtois et aide le client de façon efficace."""

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
            "description": "Récupère les informations du client",
            "parameters": {
                "type": "object",
                "properties": {
                    "telephone": {"type": "string", "description": "Numéro de téléphone du client"},
                },
                "required": ["telephone"]
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

        # Vérifier la disponibilité
        if not est_creneau_disponible(jour, heure):
            return f"Désolé, le créneau du {jour} à {heure} n'est pas disponible."

        # Récupérer/créer le client
        client = get_or_create_client(telephone)
        client_id = client.get("id")

        # Enregistrer le RDV
        enregistrer_rdv(
            client_id=client_id,
            jour=jour,
            heure=heure,
            type_client=type_client,
            prestation=prestation,
            coupe_detail=None,
            couleur_detail=None,
            duree_max=45,
            prix=30,
            avec_shampoing=False
        )
        return f"✅ RDV confirmé pour le {jour} à {heure} pour {prestation}. Merci!"

    elif tool_name == "verifier_disponibilite":
        jour = tool_input.get("jour")
        heure = tool_input.get("heure")
        disponible = est_creneau_disponible(jour, heure)
        return f"Le créneau du {jour} à {heure} est {'disponible' if disponible else 'occupé'}."

    elif tool_name == "annuler_rdv":
        client_id = tool_input.get("client_id")
        rdv_id = tool_input.get("rdv_id")
        if annuler_rdv(client_id, rdv_id):
            return "✅ Rendez-vous annulé avec succès."
        return "❌ Erreur lors de l'annulation."

    elif tool_name == "get_services":
        services = get_services()
        return f"Services disponibles: {', '.join(services)}"

    elif tool_name == "get_client_info":
        client = get_or_create_client(telephone)
        info = f"Nom: {client.get('nom', 'Non renseigné')}, Visites: {client.get('nb_visites', 0)}"
        update_client_context(telephone, client_id=client.get("id"), nom=client.get("nom"))
        return info

    return "Fonction inconnue."

# ====================================================
# AGENT PRINCIPAL AVEC GPT-4o
# ====================================================
def run_agent(message_user: str, telephone: str) -> str:
    """
    Exécute l'agent GPT-4o avec function calling.
    Prend le message utilisateur et retourne la réponse de l'agent.
    """

    if not client_openai:
        return "⚠️ Erreur: API OpenAI non configurée. Vérifiez votre clé API."

    # Ajouter le message utilisateur à l'historique
    add_to_history(telephone, "user", message_user)

    # Préparer les messages avec le system prompt en premier
    messages = [{"role": "system", "content": build_system_prompt()}] + get_conversation_history(telephone)

    # Appeler GPT-4o avec function calling
    try:
        response = client_openai.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.7,
            max_tokens=500,
        )
    except Exception as e:
        print(f"Erreur GPT-4o: {e}")
        return "Désolé, une erreur s'est produite. Pouvez-vous répéter?"

    # Traiter la réponse
    choice = response.choices[0]

    # Si GPT-4o veut appeler une fonction
    if choice.message.tool_calls:
        # Exécuter les appels de fonction
        for tool_call in choice.message.tool_calls:
            tool_name = tool_call.function.name
            tool_input = json.loads(tool_call.function.arguments)
            tool_result = process_tool_call(tool_name, tool_input, telephone)

            # Ajouter le résultat à l'historique
            add_to_history(telephone, "tool", f"{tool_name}: {tool_result}")

        # Relancer GPT-4o avec le résultat des fonctions
        messages = [{"role": "system", "content": build_system_prompt()}] + get_conversation_history(telephone)
        response = client_openai.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.7,
            max_tokens=500,
        )
        choice = response.choices[0]

    # Extraire la réponse texte
    response_text = choice.message.content

    # Ajouter la réponse à l'historique
    add_to_history(telephone, "assistant", response_text)

    return response_text

# ====================================================
# ENDPOINT PRINCIPAL
# ====================================================
@app.post("/appel", response_class=PlainTextResponse)
def handle_sms(From: str = Form(...), Body: str = Form(...)):
    """Endpoint unique qui traite tous les appels/SMS."""
    telephone = From.replace("+", "").replace(" ", "")
    message = Body.strip()

    # Exécuter l'agent
    response = run_agent(message, telephone)

    # Générer la voix
    audio_path = tts_voice(response)

    # Créer la réponse Twilio
    twiml = VoiceResponse()
    twiml.play(f"{BASE_URL}/audio/{audio_path.split('/')[-1]}")
    twiml.gather(
        num_digits=1,
        action="/appel",
        method="POST",
        timeout=10,
    )

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
    print("\n" + "="*60)
    print("🎤 AGENT BARBERSHOP — MODE CONSOLE")
    print("="*60)
    print(f"Salon: {NOM_SALON}")
    print(f"Horaires: {HORAIRE_OUVERTURE} - {HORAIRE_FERMETURE}")
    print(f"Jours: {', '.join(JOURS_OUVERTS)}")
    print("\nTape 'quit' pour quitter\n")
    print("="*60 + "\n")

    # Numéro de test
    test_phone = "0600000000"

    while True:
        user_input = input("👤 Vous: ").strip()
        if user_input.lower() == "quit":
            print("\n👋 Au revoir!")
            break
        if not user_input:
            continue

        # Exécuter l'agent
        response = run_agent(user_input, test_phone)
        print(f"🤖 Agent: {response}\n")
