# ====================================================
# AGENT IA COIFFEUR — VERSION TÉLÉPHONE + VOIX NATURELLE
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
# 👋 POUR CONFIGURER CE SALON :
# Recherche "À PERSONNALISER" dans ce fichier
# et remplace les valeurs par celles du client.
# Tu peux aussi dire à ton IA PyCharm :
# "Remplace toutes les valeurs de configuration
#  par celles-ci : [liste des infos du salon]"
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
# Les prix ci-dessus sont les valeurs par défaut.
# Au démarrage de chaque appel ils sont remplacés
# dynamiquement par les données de la table "Service"
# filtrée par salon_id (identifié via le numéro Twilio).

openai.api_key = os.getenv("API_KEY")

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

app.state.jour_tmp = None
app.state.creneau_tmp = None
app.state.heure_defaut_tmp = None
app.state.heure_tmp = None
app.state.type_tmp = None
app.state.prestation_tmp = None
app.state.duree_min_tmp = None
app.state.duree_max_tmp = None
app.state.coupe_detail_tmp = None
app.state.couleur_detail_tmp = None
app.state.gros_changement_tmp = False
app.state.shampoing_tmp = False
app.state.prix_tmp = None
app.state.client_id_tmp = None
app.state.client_nouveau_tmp = False
app.state.salon_id_tmp = None
# Prix dynamiques (écrasés à chaque appel via load_prix_from_base44)
app.state.prix_homme_coupe = None
app.state.prix_homme_couleur = None
app.state.prix_femme_coupe = None
app.state.prix_femme_couleur = None

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

EXEMPLES_FORMULATIONS_CLIENTS = [
    "Je veux prendre rendez-vous",
    "Je voudrais réserver une coupe",
    "Vous avez une dispo demain matin ?",
    "Possible mardi prochain en fin d'après-midi ?",
    "J'aimerais un créneau vers 15h30",
    "Est-ce que vous avez de la place après-demain ?",
    "Je peux passer demain ?",
    "Je veux booker un rendez-vous coiffure",
    "Je souhaite planifier un brushing",
    "Tu peux me caler un rendez-vous ?",
    "Je voudrais venir vendredi à 10h",
    "Il me faudrait une coloration mardi",
    "Un rendez-vous pour une coupe homme samedi",
    "Vous avez un horaire libre demain soir ?",
    "Dispo le 2026-03-10 à 15:30 ?",
    "Le 14 janvier matin ça marche ?",
    "Mercredi prochain en après-midi",
    "Je suis libre en fin d'après-midi",
    "On peut fixer un créneau ?",
    "Je cherche un rendez-vous rapide aujourd'hui",
]


# ====================================================
# SUPABASE — FONCTIONS CLIENT & RDV
# ====================================================

def get_or_create_client(telephone: str) -> dict:
    """
    Cherche le client par son numéro.
    S'il existe → retourne sa fiche + dernière prestation.
    S'il n'existe pas → crée une fiche vide et la retourne.
    """
    try:
        result = supabase.table("clients")\
            .select("*")\
            .eq("telephone", telephone)\
            .execute()
        if result.data:
            client = result.data[0]
            # Récupère le dernier RDV confirmé
            rdv_result = supabase.table("rendez_vous")\
                .select("prestation, jour")\
                .eq("client_id", client["id"])\
                .eq("statut", "confirme")\
                .order("jour", desc=True)\
                .limit(1)\
                .execute()
            if rdv_result.data:
                client["derniere_prestation"] = rdv_result.data[0].get("prestation")
                client["derniere_date"] = rdv_result.data[0].get("jour")
            else:
                client["derniere_prestation"] = None
                client["derniere_date"] = None
            return client
        nouveau = supabase.table("clients")\
            .insert({"telephone": telephone})\
            .execute()
        c = nouveau.data[0]
        c["derniere_prestation"] = None
        c["derniere_date"] = None
        return c
    except Exception as e:
        print(f"Erreur Supabase get_or_create_client: {e}")
        return {"id": None, "telephone": telephone, "nom": None, "nb_visites": 0,
                "derniere_prestation": None, "derniere_date": None}


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


# ====================================================
# BASE44 — INTÉGRATION MULTI-SALON
# ====================================================

def get_salon_by_twilio(twilio_number: str) -> dict | None:
    """Identifie le salon via son numéro Twilio. Retourne la fiche salon ou None."""
    try:
        result = supabase.table("Salon")\
            .select("*")\
            .eq("twilio_number", twilio_number)\
            .limit(1)\
            .execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"Erreur get_salon_by_twilio: {e}")
        return None


def get_services_from_base44(salon_id: str) -> list:
    """Récupère les services actifs d'un salon depuis Supabase table 'Service'.
    Retourne une liste de dicts : name, price, duration_minutes, category.
    """
    try:
        result = supabase.table("Service")\
            .select("name, price, duration_minutes, category")\
            .eq("salon_id", salon_id)\
            .execute()
        return result.data or []
    except Exception as e:
        print(f"Erreur get_services_from_base44: {e}")
        return []


def get_employees_from_base44(salon_id: str) -> list:
    """Récupère les employés actifs d'un salon depuis Supabase table 'Employee'.
    Retourne une liste de dicts : full_name, specialties, work_start, work_end, working_days.
    """
    try:
        result = supabase.table("Employee")\
            .select("full_name, specialties, work_start, work_end, working_days")\
            .eq("salon_id", salon_id)\
            .eq("is_active", True)\
            .execute()
        return result.data or []
    except Exception as e:
        print(f"Erreur get_employees_from_base44: {e}")
        return []


def load_prix_from_base44(salon_id: str):
    """Charge les prix dynamiquement depuis Supabase et les stocke dans app.state.
    Construit les 4 dicts de prix à partir de la table 'Service'.
    Colonnes attendues : name (str), price (int), category (str).
    """
    services = get_services_from_base44(salon_id)
    if not services:
        return  # Garde les valeurs par défaut

    homme_coupe = {}
    homme_couleur = {}
    femme_coupe = {}
    femme_couleur = {}

    for svc in services:
        name = (svc.get("name") or "").lower().replace(" ", "_").replace("-", "_")
        price = svc.get("price") or 0
        category = (svc.get("category") or "").lower()

        if "homme" in category and "coupe" in category:
            homme_coupe[name] = price
        elif "homme" in category and "couleur" in category:
            homme_couleur[name] = price
        elif "femme" in category and "coupe" in category:
            femme_coupe[name] = price
        elif "femme" in category and "couleur" in category:
            femme_couleur[name] = price

    if homme_coupe:
        app.state.prix_homme_coupe = homme_coupe
    if homme_couleur:
        app.state.prix_homme_couleur = homme_couleur
    if femme_coupe:
        app.state.prix_femme_coupe = femme_coupe
    if femme_couleur:
        app.state.prix_femme_couleur = femme_couleur


def sync_rdv_to_base44(rdv_data: dict):
    """Écrit le RDV confirmé dans la table Supabase 'Appointment'.
    rdv_data doit contenir : salon_id, client_name, client_phone,
    employee_id, service_id, date, time.
    """
    try:
        supabase.table("Appointment").insert({
            "salon_id":     rdv_data.get("salon_id"),
            "client_name":  rdv_data.get("client_name"),
            "client_phone": rdv_data.get("client_phone"),
            "employee_id":  rdv_data.get("employee_id"),
            "service_id":   rdv_data.get("service_id"),
            "date":         rdv_data.get("date"),
            "time":         rdv_data.get("time"),
            "status":       "confirmed",
        }).execute()
    except Exception as e:
        print(f"Erreur sync_rdv_to_base44: {e}")


# ====================================================
# UTILITAIRE : Convertir texte → voix naturelle (mp3)
# ====================================================
def tts_voice(message):
    audio_id = str(uuid.uuid4()) + ".mp3"
    path = f"audio/{audio_id}"
    os.makedirs("audio", exist_ok=True)
    with open(path, "wb") as f:
        result = openai.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=message,
        )
        audio_bytes = result.read() if hasattr(result, "read") else bytes(result)
        f.write(audio_bytes)
    return path


# ====================================================
# UTILITAIRE : Nettoyer et comprendre date + heure
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
    return d.weekday() in [1, 2, 3, 4, 5]


def contient_mot(m, mots):
    return any(mot in m for mot in mots)


def extraire_type_prestation(message):
    m = normaliser_texte(message)
    if "homme" in m:
        return "homme"
    if "femme" in m:
        return "femme"
    return None


def extraire_prestation(message):
    m = normaliser_texte(message)
    a_coupe = "coupe" in m
    a_couleur = "couleur" in m or "coloration" in m
    services = []
    if "brushing" in m:
        services.append("brushing")
    if "permanente" in m or "permanent" in m:
        services.append("permanente")
    if "mise en plis" in m or "mise en pli" in m:
        services.append("mise_en_plis")
    if "lissage" in m:
        services.append("lissage")
    if "soin" in m:
        services.append("soin")
    if len(services) >= 2:
        return "combo:" + ",".join(sorted(set(services)))
    if "lissage" in m and "soin" in m:
        return "lissage_soin"
    if "brushing" in m:
        return "brushing"
    if "permanente" in m or "permanent" in m:
        return "permanente"
    if "mise en plis" in m or "mise en pli" in m:
        return "mise_en_plis"
    if "lissage" in m:
        return "lissage"
    if "soin" in m:
        return "soin"
    if a_coupe and a_couleur:
        return "coupe_couleur"
    if a_coupe:
        return "coupe"
    if a_couleur:
        return "couleur"
    if "les deux" in m or "les 2" in m or "deux" in m:
        return "coupe_couleur"
    return None


def parse_combo_prestation(prestation):
    if isinstance(prestation, str) and prestation.startswith("combo:"):
        items = prestation.split(":", 1)[1]
        return [p for p in items.split(",") if p]
    return []


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


def ajouter_minutes_hhmm(hhmm, minutes):
    heures, mins = hhmm.split(":")
    total = int(heures) * 60 + int(mins) + minutes
    total = total % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def extraire_detail_coupe_femme(message):
    m = normaliser_texte(message)
    options = ["degrade", "carré", "carre", "frange", "coupe"]
    for opt in options:
        if opt in m:
            return opt
    if "pixi" in m or "pixie" in m or "pixie cut" in m:
        return "pixie"
    if "court" in m or "courte" in m:
        return "coupe_courte"
    if "cheveux longs" in m or "longs naturels" in m or "longs naturel" in m:
        return "longs_naturels"
    return message.strip()


def extraire_detail_couleur_femme(message):
    m = normaliser_texte(message)
    if "decoloration" in m or "décoloration" in m:
        return "décoloration"
    if "meche" in m or "mèche" in m or "meches" in m or "mèches" in m:
        return "mèches"
    if "balayage" in m:
        return "balayage"
    if "ombre" in m or "ombré" in m or "ombre hair" in m or "ombré hair" in m:
        return "ombré hair"
    if "ton sur ton" in m:
        return "ton sur ton"
    if "couleur" in m or "coloration" in m:
        return "couleur"
    return message.strip()


def extraire_detail_coupe_homme(message):
    m = normaliser_texte(message)
    if "travaille" in m or "travaill" in m or "volume" in m or "au dessus" in m:
        return "travaillee"
    if "degrade" in m or "dégradé" in m or "taper" in m:
        return "normale"
    if "normale" in m or "simple" in m:
        return "normale"
    return "normale"


def extraire_detail_couleur_homme(message):
    m = normaliser_texte(message)
    if "decoloration" in m or "décoloration" in m:
        return "decoloration"
    if "meche" in m or "mèche" in m or "meches" in m or "mèches" in m or "balayage" in m:
        return "meches_balayage"
    if "fantaisie" in m or "bleu" in m or "rose" in m:
        return "fantaisie"
    if "patine" in m or "ton sur ton" in m:
        return "patine_ton_sur_ton"
    return "classique"


def detecter_gros_changement(message):
    m = normaliser_texte(message)
    return "gros changement" in m or "gros changements" in m or "changement important" in m


def extraire_shampoing(message):
    m = normaliser_texte(message)
    if "oui" in m or "ouais" in m or "yes" in m:
        return True
    if "non" in m or "pas" in m:
        return False
    if "shampoing" in m or "shampooing" in m:
        return True
    return None


def extraire_changement_prestation(message):
    m = normaliser_texte(message)
    marqueurs = ["non", "desole", "désolé", "en fait", "finalement", "plutot", "plutôt", "mais", "pas"]
    if not any(x in m for x in marqueurs):
        return None
    if "mais" in m:
        apres = m.split("mais", 1)[1].strip()
        return extraire_prestation(apres)
    return extraire_prestation(m)


def finaliser_rdv_si_possible(jour, heure, type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp, gros_changement_tmp, shampoing_tmp):
    if not jour or not heure or not type_tmp or not prestation_tmp or shampoing_tmp is None:
        return None
    coupe_min, coupe_max, couleur_min, couleur_max, duree_min, duree_max = calculer_duree_details(
        type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp, gros_changement_tmp
    )
    if shampoing_tmp:
        duree_min += 10
        duree_max += 10
    fin_estimee = ajouter_minutes_hhmm(heure, duree_max)
    if coupe_min is not None and couleur_min is not None:
        detail_duree = (
            f"{format_plage_simple(coupe_min, coupe_max)} pour la coupe + "
            f"{format_plage_simple(couleur_min, couleur_max)} pour la couleur, "
            f"donc pour le tout {format_plage_simple(duree_min, duree_max)}."
        )
    else:
        detail_duree = f"{format_plage_simple(duree_min, duree_max)}."
    return detail_duree, fin_estimee


def calculer_duree_plage(type_prestation, prestation, coupe_detail, couleur_detail, gros_changement):
    combo = parse_combo_prestation(prestation)
    if combo:
        base = {
            "brushing": (45, 60), "permanente": (120, 120),
            "mise_en_plis": (60, 60), "lissage": (45, 45), "soin": (30, 30),
        }
        total_min = sum(base.get(item, (0, 0))[0] for item in combo)
        total_max = sum(base.get(item, (0, 0))[1] for item in combo)
        return (total_min, total_max)
    if prestation == "brushing":      return (45, 60)
    if prestation == "permanente":    return (120, 120)
    if prestation == "mise_en_plis":  return (60, 60)
    if prestation == "lissage":       return (45, 45)
    if prestation == "soin":          return (30, 30)
    if prestation == "lissage_soin":  return (75, 75)
    if type_prestation == "homme":
        coupe_map = {"normale": (20, 30), "travaillee": (30, 60)}
        couleur_add = {
            "classique": (45, 75), "decoloration": (90, 150),
            "meches_balayage": (90, 120), "fantaisie": (60, 180), "patine_ton_sur_ton": (30, 45),
        }
        coupe_detail_norm = coupe_detail or "normale"
        couleur_detail_norm = couleur_detail or "classique"
        if prestation == "coupe":
            return coupe_map.get(coupe_detail_norm, (20, 30))
        if prestation == "couleur":
            return couleur_add.get(couleur_detail_norm, (45, 75))
        base_min, base_max = coupe_map.get(coupe_detail_norm, (20, 30))
        add_min, add_max = couleur_add.get(couleur_detail_norm, (45, 75))
        return (base_min + add_min, base_max + add_max)
    coupe_detail_norm = coupe_detail or "coupe"
    couleur_detail_norm = couleur_detail or "couleur"
    coupe_map = {
        "brushing": (45, 60), "carre": (45, 60), "carré": (45, 60),
        "frange": (45, 60), "degrade": (45, 75), "pixie": (30, 45),
        "coupe_courte": (30, 45), "longs_naturels": (45, 60), "coupe": (30, 45),
    }
    couleur_base = (90, 120)
    couleur_delta = {
        "balayage": (30, 60), "mèches": (30, 60), "ombré hair": (30, 60),
        "décoloration": (60, 120), "ton sur ton": (-15, 0),
    }
    if prestation == "coupe":
        return coupe_map.get(coupe_detail_norm, (30, 45))
    if prestation == "couleur":
        add_min, add_max = couleur_delta.get(couleur_detail_norm, (0, 0))
        base_min = couleur_base[0] + add_min
        base_max = couleur_base[1] + add_max
        if gros_changement:
            base_min += 30
            base_max += 40
        return (base_min, base_max)
    combo_map = {
        "brushing": (120, 150), "carre": (120, 150), "carré": (120, 150),
        "frange": (120, 150), "degrade": (120, 180), "pixie": (90, 120),
        "coupe_courte": (90, 120), "longs_naturels": (120, 180),
    }
    base_min, base_max = combo_map.get(coupe_detail_norm, (120, 165))
    add_min, add_max = couleur_delta.get(couleur_detail_norm, (0, 0))
    total_min = base_min + add_min
    total_max = base_max + add_max
    if gros_changement:
        total_min += 30
        total_max += 40
    return (total_min, total_max)


def calculer_duree_details(type_prestation, prestation, coupe_detail, couleur_detail, gros_changement):
    coupe_min = coupe_max = None
    couleur_min = couleur_max = None
    combo = parse_combo_prestation(prestation)
    if combo:
        base = {
            "brushing": (45, 60), "permanente": (120, 120),
            "mise_en_plis": (60, 60), "lissage": (45, 45), "soin": (30, 30),
        }
        total_min = sum(base.get(item, (0, 0))[0] for item in combo)
        total_max = sum(base.get(item, (0, 0))[1] for item in combo)
        return None, None, None, None, total_min, total_max
    if prestation == "brushing":      return None, None, None, None, 45, 60
    if prestation == "permanente":    return None, None, None, None, 120, 120
    if prestation == "mise_en_plis":  return None, None, None, None, 60, 60
    if prestation == "lissage":       return None, None, None, None, 45, 45
    if prestation == "soin":          return None, None, None, None, 30, 30
    if prestation == "lissage_soin":  return None, None, None, None, 75, 75
    if type_prestation == "homme":
        coupe_map = {"normale": (20, 30), "travaillee": (30, 60)}
        couleur_add = {
            "classique": (45, 75), "decoloration": (90, 150),
            "meches_balayage": (90, 120), "fantaisie": (60, 180), "patine_ton_sur_ton": (30, 45),
        }
        coupe_detail_norm = coupe_detail or "normale"
        couleur_detail_norm = couleur_detail or "classique"
        if prestation in {"coupe", "coupe_couleur"}:
            coupe_min, coupe_max = coupe_map.get(coupe_detail_norm, (20, 30))
        if prestation in {"couleur", "coupe_couleur"}:
            couleur_min, couleur_max = couleur_add.get(couleur_detail_norm, (45, 75))
    else:
        coupe_map = {
            "brushing": (45, 60), "carre": (45, 60), "carré": (45, 60),
            "frange": (45, 60), "degrade": (45, 75), "pixie": (30, 45),
            "coupe_courte": (30, 45), "longs_naturels": (45, 60), "coupe": (30, 45),
        }
        couleur_base = (90, 120)
        couleur_delta = {
            "balayage": (30, 60), "mèches": (30, 60), "ombré hair": (30, 60),
            "décoloration": (60, 120), "ton sur ton": (-15, 0),
        }
        coupe_detail_norm = coupe_detail or "coupe"
        couleur_detail_norm = couleur_detail or "couleur"
        if prestation in {"coupe", "coupe_couleur"}:
            coupe_min, coupe_max = coupe_map.get(coupe_detail_norm, (30, 45))
        if prestation in {"couleur", "coupe_couleur"}:
            add_min, add_max = couleur_delta.get(couleur_detail_norm, (0, 0))
            couleur_min, couleur_max = (couleur_base[0] + add_min, couleur_base[1] + add_max)
            if gros_changement:
                couleur_min += 30
                couleur_max += 40
    total_min = (coupe_min or 0) + (couleur_min or 0)
    total_max = (coupe_max or 0) + (couleur_max or 0)
    return coupe_min, coupe_max, couleur_min, couleur_max, total_min, total_max


def calculer_prix_details(type_prestation, prestation, coupe_detail, couleur_detail):
    # Utilise les prix dynamiques chargés depuis Supabase si disponibles,
    # sinon retombe sur les constantes par défaut.
    ph_coupe   = app.state.prix_homme_coupe   or PRIX_HOMME_COUPE
    ph_couleur = app.state.prix_homme_couleur or PRIX_HOMME_COULEUR
    pf_coupe   = app.state.prix_femme_coupe   or PRIX_FEMME_COUPE
    pf_couleur = app.state.prix_femme_couleur or PRIX_FEMME_COULEUR

    coupe_prix = None
    couleur_prix = None
    if type_prestation == "homme":
        coupe_detail_norm = coupe_detail or "normale"
        couleur_detail_norm = couleur_detail or "classique"
        if prestation in {"coupe", "coupe_couleur"}:
            coupe_prix = ph_coupe.get(coupe_detail_norm, 15)
        if prestation in {"couleur", "coupe_couleur"}:
            couleur_prix = ph_couleur.get(couleur_detail_norm, 30)
    else:
        coupe_detail_norm = coupe_detail or "coupe"
        couleur_detail_norm = couleur_detail or "couleur"
        if prestation in {"coupe", "coupe_couleur"}:
            coupe_prix = pf_coupe.get(coupe_detail_norm, 30)
        if prestation in {"couleur", "coupe_couleur"}:
            couleur_prix = pf_couleur.get(couleur_detail_norm, 30)
    total = (coupe_prix or 0) + (couleur_prix or 0)
    return coupe_prix, couleur_prix, total


def format_plage_simple(min_v, max_v):
    if min_v is None or max_v is None:
        return ""
    def fmt(value):
        if value >= 60:
            heures = value // 60
            minutes = value % 60
            if minutes == 0:
                return f"{heures}h"
            return f"{heures}h{minutes:02d}"
        return f"{value} min"
    return f"{fmt(min_v)} - {fmt(max_v)}"


def intention_rdv_probable(message):
    m = normaliser_texte(message)
    mots_rdv = [
        "rdv", "rendez vous", "rendez-vous", "reservation", "reserver",
        "booker", "book", "prendre", "planifier", "fixer", "caler",
        "dispo", "disponibilite", "creneau",
    ]
    mots_prestation = [
        "coiffure", "coiffeur", "coiffeuse", "coupe", "brushing",
        "coloration", "barbe", "shampoing",
    ]
    mots_demande = [
        "je veux", "je voudrais", "j aimerais", "je souhaite", "possible",
        "est ce que", "vous avez", "on peut",
    ]
    if contient_mot(m, mots_rdv):
        return True
    if contient_mot(m, mots_prestation) and contient_mot(m, mots_demande):
        return True
    return False


def intention_info_horaires_probable(message):
    m = normaliser_texte(message)
    mots = ["horaire", "ouvert", "ferme", "fermeture", "ouverture", "quand", "jour", "disponible"]
    return contient_mot(m, mots)


def intention_info_prix_probable(message):
    m = normaliser_texte(message)
    mots = ["prix", "tarif", "tarifs", "combien", "cout", "coût", "euro", "euros"]
    return contient_mot(m, mots)


def intention_conseil_probable(message):
    m = normaliser_texte(message)
    mots = [
        "conseil", "conseils", "renseignement", "renseignements", "infos",
        "information", "informations", "je cherche des infos", "je veux des infos",
        "j ai besoin d infos", "aidez moi", "aide moi", "je ne sais pas",
        "je sais pas", "besoin d aide", "je veux un avis", "que me conseillez vous",
        "que me conseillez", "que me recommandez", "recommandation", "recommandations",
        "besoin de conseil", "besoin de conseils", "besoin de renseignements",
        "pouvez vous m aider", "pouvez vous me conseiller", "j hesite",
        "j hesite entre", "hesite", "hesitation", "jhesite", "justement j hesite",
        "je reflechis", "je reflechie", "je suis en train de reflechir",
        "je reflechis encore", "je suis perdu", "je suis perdue",
        "je ne sais pas quoi choisir", "je ne sais pas quoi faire",
        "je ne sais pas quoi prendre", "je ne sais pas quel choisir",
        "je sais pas quoi choisir", "je sais pas quoi faire",
        "je sais pas quoi prendre", "je sais pas quel choisir",
        "je suis indecis", "je suis indecise", "je suis indecisif",
        "je suis indecisive", "je suis pas sur", "je suis pas sure",
        "je ne suis pas sur", "je ne suis pas sure", "j ai un doute",
        "j hesite encore", "je me tate", "je ne suis pas decide",
        "je ne suis pas decidee",
    ]
    return contient_mot(m, mots)


def extraire_telephone(message):
    if not message:
        return None
    digits = re.sub(r"\D", "", message)
    if len(digits) == 0:
        return None
    if len(digits) == 10:
        return digits
    if len(digits) < 10 or len(digits) > 10:
        return "INVALID_LENGTH"
    return None


def repondre_info_horaires(message):
    m = normaliser_texte(message)
    salutation = "bonjour" in m or "salut" in m
    prefix = "Bonjour. " if salutation else ""
    relance = " Quelle est votre demande ?" if salutation else ""
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    for jour in jours:
        if jour in m:
            if jour in JOURS_OUVERTS:
                return f"{prefix}Oui, le salon est ouvert le {jour}, de {HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}.{relance}"
            return f"{prefix}Non, le salon est fermé le {jour}. Nous sommes ouverts du mardi au samedi de {HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}.{relance}"
    return f"{prefix}{MESSAGE_HORAIRES}{relance}"


def repondre_info_prix(message):
    m = normaliser_texte(message)
    if app.state.type_tmp and app.state.prestation_tmp:
        coupe_prix, couleur_prix, total = calculer_prix_details(
            app.state.type_tmp, app.state.prestation_tmp,
            app.state.coupe_detail_tmp, app.state.couleur_detail_tmp,
        )
        if coupe_prix is not None and couleur_prix is not None:
            return f"{coupe_prix} + {couleur_prix} = {total} euros."
        if coupe_prix is not None:
            return f"{coupe_prix} euros."
        if couleur_prix is not None:
            return f"{couleur_prix} euros."
    if "coiffure" in m or "coupe" in m:
        return "Le prix d'une coiffure dépend de la prestation, avec un tarif à partir de 15 euros."
    if "coloration" in m:
        return "Le tarif d'une coloration dépend de la longueur et de la technique, à partir de 15 euros."
    return MESSAGE_PRIX_BASE


def indices_date_heure(message):
    m = normaliser_texte(message)
    if re.search(r"\b\d{4}-\d{1,2}-\d{1,2}\b", m):
        return True
    if re.search(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{4})?\b", m):
        return True
    if re.search(r"\b([01]?\d|2[0-3])[:h][0-5]\d\b", m):
        return True
    if re.search(r"\b([01]?\d|2[0-3])\s*(?:h|heure|heures)\b", m):
        return True
    mots = [
        "demain", "apres demain", "dans", "jour", "jours", "aujourd hui",
        "matin", "apres midi", "fin d apres midi", "soir",
        "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
        "janvier", "fevrier", "mars", "avril", "mai", "juin",
        "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
    ]
    return contient_mot(m, mots)


def classer_intention_ia(message):
    try:
        r = openai.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classe le message dans une de ces intentions uniquement: "
                        "prendre_rdv, donner_date, donner_heure, info_prix, info_horaires, presentation, inconnu. "
                        "Reponds avec le label uniquement."
                    ),
                },
                {"role": "user", "content": message or ""},
            ],
        )
        label = (r.choices[0].message.content or "").strip().lower()
        if label in {"prendre_rdv", "donner_date", "donner_heure", "info_prix", "info_horaires", "presentation", "inconnu"}:
            return label
    except Exception:
        return None
    return None


def extraction_ia_date_heure(message, reference_date):
    try:
        r = openai.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extrais date et heure d'un message client pour un rendez-vous. "
                        f"Date de reference: {reference_date.isoformat()}. "
                        "Retourne strictement un JSON avec les champs: "
                        "date_iso (YYYY-MM-DD ou null), heure_hhmm (HH:MM ou null), creneau (matin|apres-midi|fin d'apres-midi|soir|null). "
                        "Resolus les dates relatives (demain, apres-demain, mardi prochain). "
                        "Resolus aussi les expressions comme 'dans 2j' ou 'dans 2 jours'. "
                        "Exemple: si reference=2026-01-13 et message='demain matin', alors date_iso='2026-01-14' et creneau='matin'."
                        "Exemple: si reference=2026-03-06 et message='apres-demain' ou 'dans 2 jours', alors date_iso='2026-03-08'."
                    ),
                },
                {"role": "user", "content": message or ""},
            ],
        )
        brut = (r.choices[0].message.content or "").strip()
        start = brut.find("{")
        end = brut.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        payload = json.loads(brut[start:end + 1])
        date_iso = payload.get("date_iso")
        heure_hhmm = normaliser_heure(payload.get("heure_hhmm"))
        creneau = payload.get("creneau")
        if creneau and creneau not in {"matin", "apres-midi", "fin d'apres-midi", "soir"}:
            creneau = None
        if date_iso:
            try:
                datetime.strptime(date_iso, "%Y-%m-%d")
            except ValueError:
                date_iso = None
        return {"date_iso": date_iso, "heure_hhmm": heure_hhmm, "creneau": creneau}
    except Exception:
        return None


def extraire_date(texte_normalise, reference_date):
    match_dans_jours = re.search(r"\bdans\s+(\d{1,2})\s*(j|jour|jours)\b", texte_normalise)
    if match_dans_jours:
        return reference_date + timedelta(days=int(match_dans_jours.group(1)))
    if "apres demain" in texte_normalise:
        return reference_date + timedelta(days=2)
    if "demain" in texte_normalise:
        return reference_date + timedelta(days=1)
    if "aujourd hui" in texte_normalise or "aujourdhui" in texte_normalise:
        return reference_date
    match_jour = re.search(r"\b(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)(?:\s+(prochain|prochaine))?\b", texte_normalise)
    if match_jour:
        jour_cible = JOURS_FR[match_jour.group(1)]
        delta = (jour_cible - reference_date.weekday()) % 7
        if match_jour.group(2):
            delta = 7 if delta == 0 else delta + 7
        return reference_date + timedelta(days=delta)
    match_iso = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", texte_normalise)
    if match_iso:
        annee, mois, jour = map(int, match_iso.groups())
        try:
            return datetime(annee, mois, jour).date()
        except ValueError:
            return None
    match_slash = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?\b", texte_normalise)
    if match_slash:
        jour = int(match_slash.group(1))
        mois = int(match_slash.group(2))
        annee = int(match_slash.group(3)) if match_slash.group(3) else reference_date.year
        try:
            candidate = datetime(annee, mois, jour).date()
            if not match_slash.group(3) and candidate < reference_date:
                candidate = datetime(annee + 1, mois, jour).date()
            return candidate
        except ValueError:
            return None
    match_mois = re.search(r"\b(\d{1,2})\s+(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre)\b", texte_normalise)
    if match_mois:
        jour = int(match_mois.group(1))
        mois = MOIS_FR[match_mois.group(2)]
        annee = reference_date.year
        try:
            candidate = datetime(annee, mois, jour).date()
            if candidate < reference_date:
                candidate = datetime(annee + 1, mois, jour).date()
            return candidate
        except ValueError:
            return None
    return None


def extraire_heure_et_creneau(texte_normalise):
    match_hhmm = re.search(r"(?:\b(?:a|vers)\s*)?([01]?\d|2[0-3])[:h]([0-5]\d)\b", texte_normalise)
    if match_hhmm:
        return f"{int(match_hhmm.group(1)):02d}:{match_hhmm.group(2)}", None, None
    match_h = re.search(r"(?:\b(?:a|vers)\s*)?([01]?\d|2[0-3])\s*(?:h|heure|heures)\b", texte_normalise)
    if match_h:
        return f"{int(match_h.group(1)):02d}:00", None, None
    if "fin d apres midi" in texte_normalise:
        return None, "fin d'après-midi", "17:30"
    if "apres midi" in texte_normalise:
        return None, "après-midi", "15:00"
    if "matin" in texte_normalise:
        return None, "matin", "09:00"
    if "soir" in texte_normalise:
        return None, "soir", "19:00"
    return None, None, None


def extraire_jour_heure(texte, reference_date=None):
    reference_date = reference_date or date_du_jour()
    texte_normalise = normaliser_texte(texte)
    date_obj = extraire_date(texte_normalise, reference_date)
    heure, creneau, heure_par_defaut = extraire_heure_et_creneau(texte_normalise)
    jour = date_obj.isoformat() if date_obj else None
    if not jour or (not heure and not creneau):
        extraction_ia = extraction_ia_date_heure(texte, reference_date)
        if extraction_ia:
            if not jour and extraction_ia.get("date_iso"):
                jour = extraction_ia["date_iso"]
            if not heure and extraction_ia.get("heure_hhmm"):
                heure = extraction_ia["heure_hhmm"]
            if not creneau and extraction_ia.get("creneau"):
                creneau = extraction_ia["creneau"].replace("apres-midi", "après-midi")
    if not heure and creneau:
        if creneau == "matin":             heure_par_defaut = "09:00"
        elif creneau == "après-midi":      heure_par_defaut = "15:00"
        elif creneau == "fin d'après-midi": heure_par_defaut = "17:30"
        elif creneau == "soir":            heure_par_defaut = "19:00"
    return jour, heure, creneau, heure_par_defaut


# ====================================================
# L'AGENT : Intentions & réponses
# ====================================================
def presentation(nom_client=None, derniere_prestation=None, derniere_date=None):
    if nom_client and derniere_prestation and derniere_date:
        return (
            f"Bonjour {nom_client}, ravi de vous retrouver ! "
            f"Votre dernière visite était une {derniere_prestation} le {derniere_date}. "
            f"Souhaitez-vous reprendre la même prestation ?"
        )
    if nom_client:
        return f"Bonjour {nom_client}, ravi de vous retrouver au {NOM_SALON}. Que puis-je faire pour vous ?"
    return f"Bonjour, vous êtes bien au {NOM_SALON}. Je peux vous aider à prendre un rendez-vous."


def comprendre_intention(message):
    m = normaliser_texte(message)
    if intention_rdv_probable(message):
        return "prendre_rdv"
    if intention_conseil_probable(m):
        return "info_conseil"
    if intention_info_prix_probable(m):
        return "info_prix"
    if intention_info_horaires_probable(m):
        return "info_horaires"
    if indices_date_heure(m):
        if "h" in m or ":" in m or "heure" in m or "vers" in m:
            return "donner_heure"
        return "donner_date"
    if "heure" in m or "vers" in m:
        return "donner_heure"
    if "bonjour" in m or "allo" in m:
        return "presentation"
    label_ia = classer_intention_ia(message)
    if label_ia:
        return label_ia
    return "inconnu"


def est_fin_conversation(message):
    m = normaliser_texte(message)
    if not m:
        return False
    expressions_fin = [
        "merci", "merci beaucoup", "parfait merci", "super merci",
        "c est parfait", "tres bien merci", "ok merci", "d accord merci",
        "parfait", "tres bien", "c est tres bien", "c est bon", "c est ok",
        "ca me va", "ca me convient", "ca va", "c est tres bien pour moi",
        "c est parfait pour moi", "c est bon pour moi", "c est ok pour moi",
        "au revoir", "aurevoir", "bonne journee", "bonne soiree",
        "a bientot", "a plus", "bye", "ciao", "vas y c est carre"
    ]
    if any(expr in m for expr in expressions_fin):
        return True
    if "merci" in m and ("revoir" in m or "bientot" in m or "bonne journee" in m):
        return True
    return False


def reponse_ia(message):
    try:
        aujourdhui = date_du_jour()
        aujourd_hui_phrase = format_date_longue(aujourdhui)
        r = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Tu es un assistant du {NOM_SALON}, aimable et clair. "
                        f"La date actuelle est {aujourdhui.isoformat()} ({aujourd_hui_phrase}). "
                        "Tu comprends les dates relatives et les moments de la journee "
                        "(demain, apres-demain, mardi prochain, dans 2j, dans 2 jours, demain matin, fin d'apres-midi). "
                        "Exemple: si on est le 13 janvier et le client dit 'demain matin', "
                        "cela correspond au 14 janvier au matin."
                        "Exemple: si on est le 6 mars et le client dit 'apres-demain' ou 'dans 2 jours', "
                        "cela correspond au 8 mars."
                    ),
                },
                {"role": "user", "content": message}
            ]
        )
        return r.choices[0].message.content
    except Exception:
        return "Désolé, je n'ai pas compris."


def reset_state():
    """Remet à zéro tous les états temporaires."""
    app.state.jour_tmp = None
    app.state.creneau_tmp = None
    app.state.heure_defaut_tmp = None
    app.state.heure_tmp = None
    app.state.type_tmp = None
    app.state.prestation_tmp = None
    app.state.duree_min_tmp = None
    app.state.duree_max_tmp = None
    app.state.coupe_detail_tmp = None
    app.state.couleur_detail_tmp = None
    app.state.gros_changement_tmp = False
    app.state.shampoing_tmp = False
    app.state.prix_tmp = None
    app.state.client_id_tmp = None
    app.state.client_nouveau_tmp = False
    app.state.client_nom_tmp = None
    app.state.salon_id_tmp = None
    app.state.prix_homme_coupe = None
    app.state.prix_homme_couleur = None
    app.state.prix_femme_coupe = None
    app.state.prix_femme_couleur = None


# ====================================================
# TWILIO : ROUTE D'APPEL
# ====================================================
@app.post("/appel", response_class=PlainTextResponse)
async def appel(
    SpeechResult: str = Form(None),
    From: str = Form(None),
    To: str = Form(None),
):
    vr = VoiceResponse()

    # Début de l'appel — identification salon + reconnaissance client
    if SpeechResult is None:
        # Identifier le salon via le numéro Twilio composé
        salon = get_salon_by_twilio(To) if To else None
        if salon:
            app.state.salon_id_tmp = salon.get("id")
            load_prix_from_base44(app.state.salon_id_tmp)
        else:
            app.state.salon_id_tmp = None

        telephone = From or "console_test"
        client = get_or_create_client(telephone)
        app.state.client_id_tmp = client.get("id")
        nom = client.get("nom")
        app.state.client_nouveau_tmp = not bool(nom)
        app.state.client_nom_tmp = nom
        msg = presentation(
            nom,
            client.get("derniere_prestation"),
            client.get("derniere_date"),
        )
        audio = tts_voice(msg)
        vr.play(url=f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel")
        return str(vr)

    message = SpeechResult
    if est_fin_conversation(message):
        audio = tts_voice(END_CALL_MESSAGE)
        vr.play(f"{BASE_URL}/{audio}")
        vr.hangup()
        return str(vr)

    changement = extraire_changement_prestation(message)
    if changement:
        app.state.prestation_tmp = changement
        app.state.coupe_detail_tmp = None
        app.state.couleur_detail_tmp = None
        app.state.gros_changement_tmp = detecter_gros_changement(message)
        app.state.shampoing_tmp = None
        if not app.state.type_tmp:
            audio = tts_voice("D'accord. Est-ce pour une prestation homme ou femme ?")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_type")
            return str(vr)
        if app.state.type_tmp == "femme" and "coupe" in changement:
            audio = tts_voice("Quel type de coupe souhaitez-vous ? Exemples : brushing, carré, frange, dégradé.")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_coupe")
            return str(vr)
        if app.state.type_tmp == "femme" and "couleur" in changement:
            audio = tts_voice("Quel type de couleur souhaitez-vous ? Exemples : classique, décoloration, mèches, balayage, ombré hair, ton sur ton.")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_couleur")
            return str(vr)
        if app.state.type_tmp == "homme" and "coupe" in changement:
            audio = tts_voice("Pour une coupe homme, est-ce une coupe normale (dégradé/taper) ou une coupe travaillée avec du volume ?")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_coupe")
            return str(vr)
        if app.state.type_tmp == "homme" and "couleur" in changement:
            audio = tts_voice("Pour la couleur homme, est-ce une coloration classique, une décoloration, des mèches/balayage, une couleur fantaisie, ou une patine ton sur ton ?")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_couleur")
            return str(vr)
        audio = tts_voice("Souhaitez-vous un shampoing ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_shampoing")
        return str(vr)

    intention = comprendre_intention(message)

    if intention in {"prendre_rdv", "donner_date", "donner_heure"}:
        aujourdhui = date_du_jour()
        reset_state()
        jour, heure, creneau, heure_par_defaut = extraire_jour_heure(message, aujourdhui)

        if jour and not est_jour_ouvrable(jour):
            audio = tts_voice("Le salon est ouvert du mardi au samedi. Pouvez-vous proposer un autre jour s'il vous plaît ?")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_date")
            return str(vr)

        if jour and heure:
            if not est_horaire_ouverture(heure):
                audio = tts_voice(f"Nous sommes ouverts de {HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}. Pouvez-vous proposer une autre heure ?")
                app.state.jour_tmp = jour
                vr.play(f"{BASE_URL}/{audio}")
                vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
                return str(vr)
            if not app.state.type_tmp:
                app.state.jour_tmp = jour
                app.state.heure_tmp = heure
                audio = tts_voice(f"Oui, bien sûr. Je note {heure}. Est-ce pour une prestation homme ou femme ?")
                vr.play(f"{BASE_URL}/{audio}")
                vr.gather(input="speech", speechTimeout="auto", action="/appel_type")
                return str(vr)
            if not est_creneau_disponible(jour, heure):
                app.state.jour_tmp = jour
                audio = tts_voice(f"Le créneau du {jour} à {heure} est déjà pris. Pouvez-vous proposer une autre heure s'il vous plaît ?")
                vr.play(f"{BASE_URL}/{audio}")
                vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
                return str(vr)
            enregistrer_rdv(app.state.client_id_tmp, jour, heure, None, None, None, None, 0, None)
            final_msg = (
                f"Parfait ! Votre rendez-vous est bien prévu pour le {jour} à {heure}. "
                f"Vous recevrez la confirmation sur le site : {SITE_CLIENT}. "
                "Merci pour votre appel et à bientôt !"
            )
            audio = tts_voice(final_msg)
            vr.play(f"{BASE_URL}/{audio}")
            vr.hangup()
            return str(vr)

        if jour and not heure and heure_par_defaut and est_horaire_ouverture(heure_par_defaut):
            app.state.jour_tmp = jour
            app.state.creneau_tmp = creneau
            app.state.heure_defaut_tmp = heure_par_defaut
            audio = tts_voice(
                f"Je note {jour}. J'ai compris {creneau if creneau else 'ce créneau'}. "
                "Souhaitez-vous confirmer l'heure proposée ou donner une heure précise ?"
            )
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
            return str(vr)

        if jour:
            app.state.jour_tmp = jour
            audio = tts_voice("Très bien. Est-ce pour une prestation homme ou femme ?")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_type")
            return str(vr)

        audio = tts_voice(
            f"Oui, bien sûr, avec plaisir. Pour quel jour souhaitez-vous venir ? "
            f"Nous sommes le {format_date_longue(aujourdhui)}. "
            "Vous pouvez dire une date comme demain, après-demain, mardi prochain, ou le 10 avril."
        )
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_date")
        return str(vr)

    if intention == "info_horaires":
        audio = tts_voice(repondre_info_horaires(message))
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel")
        return str(vr)

    if intention == "info_conseil":
        audio = tts_voice("Bien sûr. Pouvez-vous me laisser votre numéro de téléphone ? Nous vous rappellerons dans les plus brefs délais.")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_conseil")
        return str(vr)

    if intention == "info_prix":
        audio = tts_voice(repondre_info_prix(message))
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel")
        return str(vr)

    rep = reponse_ia(message)
    audio = tts_voice(rep)
    vr.play(f"{BASE_URL}/{audio}")
    vr.gather(input="speech", speechTimeout="auto", action="/appel")
    return str(vr)


# ====================================================
# TWILIO : CAPTURE DU NOM / PRÉNOM
# ====================================================
@app.post("/appel_nom", response_class=PlainTextResponse)
async def appel_nom(SpeechResult: str = Form(None)):
    vr = VoiceResponse()
    if SpeechResult:
        nom_complet = SpeechResult.strip()
        client_id = app.state.client_id_tmp
        if client_id and nom_complet:
            mettre_a_jour_nom_client(client_id, nom_complet)
        audio = tts_voice(f"Merci {nom_complet}. Comment puis-je vous aider ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel")
        return str(vr)
    audio = tts_voice("Désolé, je n'ai pas bien entendu. Pouvez-vous répéter votre prénom et votre nom ?")
    vr.play(f"{BASE_URL}/{audio}")
    vr.gather(input="speech", speechTimeout="auto", action="/appel_nom")
    return str(vr)


# ====================================================
# TWILIO : CAPTURE DU PRÉNOM EN FIN D'APPEL (nouveau client)
# ====================================================
@app.post("/appel_nom_fin", response_class=PlainTextResponse)
async def appel_nom_fin(SpeechResult: str = Form(None)):
    vr = VoiceResponse()
    if SpeechResult:
        prenom = SpeechResult.strip()
        client_id = app.state.client_id_tmp
        if client_id and prenom:
            mettre_a_jour_nom_client(client_id, prenom)
            app.state.client_nom_tmp = prenom
        audio = tts_voice(f"Merci {prenom} ! À bientôt au {NOM_SALON}. Bonne journée !")
        vr.play(f"{BASE_URL}/{audio}")
        vr.hangup()
        reset_state()
        return str(vr)
    audio = tts_voice("Désolé, je n'ai pas bien entendu. Pouvez-vous répéter votre prénom ?")
    vr.play(f"{BASE_URL}/{audio}")
    vr.gather(input="speech", speechTimeout="auto", action="/appel_nom_fin")
    return str(vr)


# ====================================================
# TWILIO : CAPTURE DU JOUR
# ====================================================
@app.post("/appel_date", response_class=PlainTextResponse)
async def appel_date(SpeechResult: str = Form(None)):
    vr = VoiceResponse()
    message = SpeechResult or ""
    if est_fin_conversation(message):
        audio = tts_voice(END_CALL_MESSAGE)
        vr.play(f"{BASE_URL}/{audio}")
        vr.hangup()
        return str(vr)
    jour, heure, creneau, heure_par_defaut = extraire_jour_heure(message, date_du_jour())
    app.state.type_tmp = None
    app.state.heure_tmp = None
    app.state.prestation_tmp = None
    app.state.coupe_detail_tmp = None
    app.state.couleur_detail_tmp = None
    app.state.gros_changement_tmp = False
    app.state.shampoing_tmp = False
    if not jour:
        audio = tts_voice("Je n'ai pas bien compris le jour. Vous pouvez dire demain, après-demain, mardi prochain, ou une date comme le 10 avril.")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_date")
        return str(vr)
    if not est_jour_ouvrable(jour):
        audio = tts_voice("Le salon est ouvert du mardi au samedi. Pouvez-vous proposer un autre jour s'il vous plaît ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_date")
        return str(vr)
    if heure:
        if not est_horaire_ouverture(heure):
            app.state.jour_tmp = jour
            audio = tts_voice(f"Nous sommes ouverts de {HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}. Pouvez-vous proposer une autre heure ?")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
            return str(vr)
        app.state.jour_tmp = jour
        app.state.heure_tmp = heure
        audio = tts_voice(f"Oui, bien sûr. Je note {heure}. Est-ce pour une prestation homme ou femme ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_type")
        return str(vr)
    app.state.jour_tmp = jour
    app.state.creneau_tmp = creneau
    app.state.heure_defaut_tmp = heure_par_defaut
    audio = tts_voice("Très bien. Est-ce pour une prestation homme ou femme ?")
    vr.play(f"{BASE_URL}/{audio}")
    vr.gather(input="speech", speechTimeout="auto", action="/appel_type")
    return str(vr)


# ====================================================
# TWILIO : CAPTURE DU TYPE DE PRESTATION
# ====================================================
@app.post("/appel_type", response_class=PlainTextResponse)
async def appel_type(SpeechResult: str = Form(None)):
    vr = VoiceResponse()
    message = SpeechResult or ""
    if est_fin_conversation(message):
        audio = tts_voice(END_CALL_MESSAGE)
        vr.play(f"{BASE_URL}/{audio}")
        vr.hangup()
        return str(vr)
    type_prestation = extraire_type_prestation(message)
    if not type_prestation:
        audio = tts_voice("Pouvez-vous préciser s'il s'agit d'une prestation homme ou femme, s'il vous plaît ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_type")
        return str(vr)
    app.state.type_tmp = type_prestation
    audio = tts_voice("Très bien. Souhaitez-vous une coupe, une couleur, un brushing, une permanente, une mise en plis, un lissage ou un soin ?")
    vr.play(f"{BASE_URL}/{audio}")
    vr.gather(input="speech", speechTimeout="auto", action="/appel_prestation")
    return str(vr)


# ====================================================
# TWILIO : CAPTURE DE LA PRESTATION
# ====================================================
@app.post("/appel_prestation", response_class=PlainTextResponse)
async def appel_prestation(SpeechResult: str = Form(None)):
    vr = VoiceResponse()
    message = SpeechResult or ""
    if est_fin_conversation(message):
        audio = tts_voice(END_CALL_MESSAGE)
        vr.play(f"{BASE_URL}/{audio}")
        vr.hangup()
        return str(vr)
    changement = extraire_changement_prestation(message)
    if changement:
        app.state.prestation_tmp = changement
        app.state.coupe_detail_tmp = None
        app.state.couleur_detail_tmp = None
        app.state.gros_changement_tmp = detecter_gros_changement(message)
    prestation = extraire_prestation(message)
    if not prestation:
        audio = tts_voice("Pouvez-vous préciser: coupe, couleur, ou les deux ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_prestation")
        return str(vr)
    app.state.prestation_tmp = prestation
    if detecter_gros_changement(message):
        app.state.gros_changement_tmp = True
    coupe_min, coupe_max, couleur_min, couleur_max, duree_min, duree_max = calculer_duree_details(
        app.state.type_tmp, prestation, app.state.coupe_detail_tmp,
        app.state.couleur_detail_tmp, app.state.gros_changement_tmp,
    )
    if app.state.shampoing_tmp:
        duree_min += 10
        duree_max += 10
    app.state.duree_min_tmp = duree_min
    app.state.duree_max_tmp = duree_max

    if prestation == "brushing":
        app.state.coupe_detail_tmp = None
        app.state.couleur_detail_tmp = None
        app.state.duree_min_tmp, app.state.duree_max_tmp = (45, 60)
        if app.state.heure_tmp is None and app.state.creneau_tmp is None:
            audio = tts_voice("Très bien. À quelle heure souhaitez-vous venir ? Donnez l'heure au format HH:MM, par exemple 15:30.")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
            return str(vr)
    if app.state.type_tmp == "femme" and "coupe" in prestation and not app.state.coupe_detail_tmp:
        audio = tts_voice("Quel type de coupe souhaitez-vous ? Exemples : brushing, carré, frange, dégradé.")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_coupe")
        return str(vr)
    if app.state.type_tmp == "femme" and "couleur" in prestation and not app.state.couleur_detail_tmp:
        audio = tts_voice("Quel type de couleur souhaitez-vous ? Exemples : classique, décoloration, mèches, balayage, ombré hair, ton sur ton.")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_couleur")
        return str(vr)
    if app.state.type_tmp == "homme" and "coupe" in prestation and not app.state.coupe_detail_tmp:
        audio = tts_voice("Pour une coupe homme, est-ce une coupe normale (dégradé/taper) ou une coupe travaillée avec du volume ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_coupe")
        return str(vr)
    if app.state.type_tmp == "homme" and "couleur" in prestation and not app.state.couleur_detail_tmp:
        audio = tts_voice("Pour la couleur homme, est-ce une coloration classique, une décoloration, des mèches/balayage, une couleur fantaisie, ou une patine ton sur ton ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_couleur")
        return str(vr)

    if app.state.heure_tmp:
        if app.state.shampoing_tmp is None:
            audio = tts_voice("Souhaitez-vous un shampoing ?")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_shampoing")
            return str(vr)
        jour = app.state.jour_tmp
        heure = app.state.heure_tmp
        if not est_creneau_disponible(jour, heure):
            audio = tts_voice(f"Le créneau du {jour} à {heure} est déjà pris. Pouvez-vous proposer une autre heure s'il vous plaît ?")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
            return str(vr)
        fin_estimee = ajouter_minutes_hhmm(heure, duree_max)
        coupe_prix, couleur_prix, prix_total = calculer_prix_details(
            app.state.type_tmp, prestation,
            app.state.coupe_detail_tmp, app.state.couleur_detail_tmp
        )
        enregistrer_rdv(
            app.state.client_id_tmp, jour, heure,
            app.state.type_tmp, prestation,
            app.state.coupe_detail_tmp, app.state.couleur_detail_tmp,
            duree_max, prix_total, app.state.shampoing_tmp
        )
        # Synchronisation Base44 — écriture dans table Appointment
        sync_rdv_to_base44({
            "salon_id":     app.state.salon_id_tmp,
            "client_name":  getattr(app.state, "client_nom_tmp", None),
            "client_phone": From,
            "employee_id":  None,  # à renseigner si sélection employé ajoutée
            "service_id":   None,  # à renseigner si mapping service ajouté
            "date":         jour,
            "time":         heure,
        })
        if coupe_min is not None and couleur_min is not None:
            detail_duree = (
                f"{format_plage_simple(coupe_min, coupe_max)} pour la coupe + "
                f"{format_plage_simple(couleur_min, couleur_max)} pour la couleur, "
                f"donc pour le tout {format_plage_simple(duree_min, duree_max)}."
            )
        else:
            detail_duree = f"{format_plage_simple(duree_min, duree_max)}."
        final_msg = (
            f"Parfait ! Votre rendez-vous est bien prévu pour le {jour} à {heure}. "
            f"Durée estimée {detail_duree} "
            f"Fin prévue vers {fin_estimee}. "
            f"Vous recevrez la confirmation sur le site : {SITE_CLIENT}. "
        )
        if getattr(app.state, "client_nouveau_tmp", False):
            final_msg += "Pour finir, puis-je avoir votre prénom afin de l'enregistrer pour vos prochaines visites ?"
            audio = tts_voice(final_msg)
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_nom_fin")
        else:
            final_msg += "Merci pour votre appel et à bientôt !"
            audio = tts_voice(final_msg)
            vr.play(f"{BASE_URL}/{audio}")
            vr.hangup()
            reset_state()
        return str(vr)

    if app.state.shampoing_tmp is None:
        audio = tts_voice("Souhaitez-vous un shampoing ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_shampoing")
        return str(vr)

    creneau = app.state.creneau_tmp
    if creneau:
        audio = tts_voice(f"Très bien, je note {app.state.jour_tmp} en {creneau}. À quelle heure précise souhaitez-vous venir, s'il vous plaît ? Donnez l'heure au format HH:MM, par exemple 15:30.")
    else:
        audio = tts_voice("Très bien. À quelle heure souhaitez-vous venir ? Donnez l'heure au format HH:MM, par exemple 15:30.")
    vr.play(f"{BASE_URL}/{audio}")
    vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
    return str(vr)


# ====================================================
# TWILIO : DETAIL COUPE
# ====================================================
@app.post("/appel_detail_coupe", response_class=PlainTextResponse)
async def appel_detail_coupe(SpeechResult: str = Form(None)):
    vr = VoiceResponse()
    message = SpeechResult or ""
    if est_fin_conversation(message):
        audio = tts_voice(END_CALL_MESSAGE)
        vr.play(f"{BASE_URL}/{audio}")
        vr.hangup()
        return str(vr)
    changement = extraire_changement_prestation(message)
    if changement:
        app.state.prestation_tmp = changement
        app.state.coupe_detail_tmp = None
        app.state.couleur_detail_tmp = None
        app.state.gros_changement_tmp = detecter_gros_changement(message)
        audio = tts_voice("D'accord. Souhaitez-vous une coupe, une couleur, un brushing, une permanente, une mise en plis, un lissage ou un soin ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_prestation")
        return str(vr)
    if app.state.type_tmp == "homme":
        app.state.coupe_detail_tmp = extraire_detail_coupe_homme(message)
    else:
        app.state.coupe_detail_tmp = extraire_detail_coupe_femme(message)
    app.state.duree_min_tmp, app.state.duree_max_tmp = calculer_duree_plage(
        app.state.type_tmp, app.state.prestation_tmp,
        app.state.coupe_detail_tmp, app.state.couleur_detail_tmp, app.state.gros_changement_tmp,
    )
    if app.state.prestation_tmp and "couleur" in app.state.prestation_tmp and not app.state.couleur_detail_tmp:
        if app.state.type_tmp == "homme":
            audio = tts_voice("Pour la couleur homme, est-ce une coloration classique, une décoloration, des mèches/balayage, une couleur fantaisie, ou une patine ton sur ton ?")
        else:
            audio = tts_voice("Quel type de couleur souhaitez-vous ? Exemples : classique, décoloration, mèches, balayage, ombré hair, ton sur ton.")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_couleur")
        return str(vr)
    if app.state.heure_tmp:
        if app.state.shampoing_tmp is None:
            audio = tts_voice("Souhaitez-vous un shampoing ?")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_shampoing")
            return str(vr)
        result = finaliser_rdv_si_possible(
            app.state.jour_tmp, app.state.heure_tmp, app.state.type_tmp,
            app.state.prestation_tmp, app.state.coupe_detail_tmp,
            app.state.couleur_detail_tmp, app.state.gros_changement_tmp, app.state.shampoing_tmp,
        )
        if result:
            detail_duree, fin_estimee = result
            coupe_prix, couleur_prix, prix_total = calculer_prix_details(
                app.state.type_tmp, app.state.prestation_tmp,
                app.state.coupe_detail_tmp, app.state.couleur_detail_tmp
            )
            enregistrer_rdv(
                app.state.client_id_tmp, app.state.jour_tmp, app.state.heure_tmp,
                app.state.type_tmp, app.state.prestation_tmp,
                app.state.coupe_detail_tmp, app.state.couleur_detail_tmp,
                app.state.duree_max_tmp, prix_total, app.state.shampoing_tmp
            )
            final_msg = (
                f"Parfait ! Votre rendez-vous est bien prévu pour le {app.state.jour_tmp} à {app.state.heure_tmp}. "
                f"Durée estimée {detail_duree} Fin prévue vers {fin_estimee}. "
                f"Vous recevrez la confirmation sur le site : {SITE_CLIENT}. Merci pour votre appel et à bientôt !"
            )
            audio = tts_voice(final_msg)
            vr.play(f"{BASE_URL}/{audio}")
            vr.hangup()
            reset_state()
            return str(vr)
    audio = tts_voice("Merci. À quelle heure souhaitez-vous venir ? Donnez l'heure au format HH:MM, par exemple 15:30.")
    vr.play(f"{BASE_URL}/{audio}")
    vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
    return str(vr)


# ====================================================
# TWILIO : DETAIL COULEUR
# ====================================================
@app.post("/appel_detail_couleur", response_class=PlainTextResponse)
async def appel_detail_couleur(SpeechResult: str = Form(None)):
    vr = VoiceResponse()
    message = SpeechResult or ""
    if est_fin_conversation(message):
        audio = tts_voice(END_CALL_MESSAGE)
        vr.play(f"{BASE_URL}/{audio}")
        vr.hangup()
        return str(vr)
    changement = extraire_changement_prestation(message)
    if changement:
        app.state.prestation_tmp = changement
        app.state.coupe_detail_tmp = None
        app.state.couleur_detail_tmp = None
        app.state.gros_changement_tmp = detecter_gros_changement(message)
        audio = tts_voice("D'accord. Souhaitez-vous une coupe, une couleur, un brushing, une permanente, une mise en plis, un lissage ou un soin ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_prestation")
        return str(vr)
    if detecter_gros_changement(message):
        app.state.gros_changement_tmp = True
    if app.state.type_tmp == "homme":
        app.state.couleur_detail_tmp = extraire_detail_couleur_homme(message)
    else:
        app.state.couleur_detail_tmp = extraire_detail_couleur_femme(message)
    app.state.duree_min_tmp, app.state.duree_max_tmp = calculer_duree_plage(
        app.state.type_tmp, app.state.prestation_tmp,
        app.state.coupe_detail_tmp, app.state.couleur_detail_tmp, app.state.gros_changement_tmp,
    )
    if app.state.prestation_tmp and "coupe" in app.state.prestation_tmp and not app.state.coupe_detail_tmp:
        if app.state.type_tmp == "homme":
            audio = tts_voice("Pour une coupe homme, est-ce une coupe normale (dégradé/taper) ou une coupe travaillée avec du volume ?")
        else:
            audio = tts_voice("Quel type de coupe souhaitez-vous ? Exemples : brushing, carré, frange, dégradé.")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_coupe")
        return str(vr)
    if app.state.heure_tmp:
        if app.state.shampoing_tmp is None:
            audio = tts_voice("Souhaitez-vous un shampoing ?")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_shampoing")
            return str(vr)
        result = finaliser_rdv_si_possible(
            app.state.jour_tmp, app.state.heure_tmp, app.state.type_tmp,
            app.state.prestation_tmp, app.state.coupe_detail_tmp,
            app.state.couleur_detail_tmp, app.state.gros_changement_tmp, app.state.shampoing_tmp,
        )
        if result:
            detail_duree, fin_estimee = result
            coupe_prix, couleur_prix, prix_total = calculer_prix_details(
                app.state.type_tmp, app.state.prestation_tmp,
                app.state.coupe_detail_tmp, app.state.couleur_detail_tmp
            )
            enregistrer_rdv(
                app.state.client_id_tmp, app.state.jour_tmp, app.state.heure_tmp,
                app.state.type_tmp, app.state.prestation_tmp,
                app.state.coupe_detail_tmp, app.state.couleur_detail_tmp,
                app.state.duree_max_tmp, prix_total, app.state.shampoing_tmp
            )
            final_msg = (
                f"Parfait ! Votre rendez-vous est bien prévu pour le {app.state.jour_tmp} à {app.state.heure_tmp}. "
                f"Durée estimée {detail_duree} Fin prévue vers {fin_estimee}. "
                f"Vous recevrez la confirmation sur le site : {SITE_CLIENT}. Merci pour votre appel et à bientôt !"
            )
            audio = tts_voice(final_msg)
            vr.play(f"{BASE_URL}/{audio}")
            vr.hangup()
            reset_state()
            return str(vr)
    audio = tts_voice("Merci. À quelle heure souhaitez-vous venir ? Donnez l'heure au format HH:MM, par exemple 15:30.")
    vr.play(f"{BASE_URL}/{audio}")
    vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
    return str(vr)


# ====================================================
# TWILIO : CAPTURE CONSEIL
# ====================================================
@app.post("/appel_conseil", response_class=PlainTextResponse)
async def appel_conseil(SpeechResult: str = Form(None)):
    vr = VoiceResponse()
    message = SpeechResult or ""
    if est_fin_conversation(message):
        audio = tts_voice(END_CALL_MESSAGE)
        vr.play(f"{BASE_URL}/{audio}")
        vr.hangup()
        return str(vr)
    tel = extraire_telephone(message)
    if tel == "INVALID_LENGTH":
        audio = tts_voice("Le numéro n'est pas valide. Merci de le redonner.")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_conseil")
        return str(vr)
    if not tel:
        audio = tts_voice("Je n'ai pas bien compris le numéro. Merci de le redonner.")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_conseil")
        return str(vr)
    audio = tts_voice("Merci. Nous vous rappellerons dans les plus brefs délais.")
    vr.play(f"{BASE_URL}/{audio}")
    vr.hangup()
    return str(vr)


# ====================================================
# TWILIO : CAPTURE SHAMPOING
# ====================================================
@app.post("/appel_shampoing", response_class=PlainTextResponse)
async def appel_shampoing(SpeechResult: str = Form(None)):
    vr = VoiceResponse()
    message = SpeechResult or ""
    _, heure_message, _, _ = extraire_jour_heure(message, date_du_jour())
    changement = extraire_changement_prestation(message)
    if changement:
        app.state.prestation_tmp = changement
        app.state.coupe_detail_tmp = None
        app.state.couleur_detail_tmp = None
        app.state.gros_changement_tmp = detecter_gros_changement(message)
        audio = tts_voice("D'accord. Souhaitez-vous une coupe, une couleur, un brushing, une permanente, une mise en plis, un lissage ou un soin ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_prestation")
        return str(vr)
    shampoing = extraire_shampoing(message)
    if heure_message:
        if not est_horaire_ouverture(heure_message):
            audio = tts_voice(f"Cette heure est en dehors des horaires d'ouverture ({HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}). Pouvez-vous proposer une autre heure ?")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
            return str(vr)
        app.state.heure_tmp = heure_message
        if shampoing is None:
            audio = tts_voice(f"D'accord, je note {heure_message}. Souhaitez-vous un shampoing ? Répondez par oui ou non.")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_shampoing")
            return str(vr)
    if shampoing is None:
        if est_fin_conversation(message):
            audio = tts_voice(END_CALL_MESSAGE)
            vr.play(f"{BASE_URL}/{audio}")
            vr.hangup()
            return str(vr)
        audio = tts_voice("Souhaitez-vous un shampoing ? Répondez par oui ou non.")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_shampoing")
        return str(vr)
    app.state.shampoing_tmp = shampoing
    if app.state.heure_tmp and app.state.jour_tmp:
        jour = app.state.jour_tmp
        heure = app.state.heure_tmp
        coupe_min, coupe_max, couleur_min, couleur_max, duree_min, duree_max = calculer_duree_details(
            app.state.type_tmp, app.state.prestation_tmp,
            app.state.coupe_detail_tmp, app.state.couleur_detail_tmp, app.state.gros_changement_tmp,
        )
        if shampoing:
            duree_min += 10
            duree_max += 10
        fin_estimee = ajouter_minutes_hhmm(heure, duree_max)
        coupe_prix, couleur_prix, prix_total = calculer_prix_details(
            app.state.type_tmp, app.state.prestation_tmp,
            app.state.coupe_detail_tmp, app.state.couleur_detail_tmp
        )
        enregistrer_rdv(
            app.state.client_id_tmp, jour, heure,
            app.state.type_tmp, app.state.prestation_tmp,
            app.state.coupe_detail_tmp, app.state.couleur_detail_tmp,
            duree_max, prix_total, shampoing
        )
        if coupe_min is not None and couleur_min is not None:
            detail_duree = (
                f"{format_plage_simple(coupe_min, coupe_max)} pour la coupe + "
                f"{format_plage_simple(couleur_min, couleur_max)} pour la couleur, "
                f"donc pour le tout {format_plage_simple(duree_min, duree_max)}."
            )
        else:
            detail_duree = f"{format_plage_simple(duree_min, duree_max)}."
        final_msg = (
            f"Parfait ! Votre rendez-vous est bien prévu pour le {jour} à {heure}. "
            f"Durée estimée {detail_duree} Fin prévue vers {fin_estimee}. "
            f"Vous recevrez la confirmation sur le site : {SITE_CLIENT}. Merci pour votre appel et à bientôt !"
        )
        audio = tts_voice(final_msg)
        vr.play(f"{BASE_URL}/{audio}")
        vr.hangup()
        reset_state()
        return str(vr)
    audio = tts_voice("Merci. À quelle heure souhaitez-vous venir ? Donnez l'heure au format HH:MM, par exemple 15:30.")
    vr.play(f"{BASE_URL}/{audio}")
    vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
    return str(vr)


# ====================================================
# TWILIO : CAPTURE DE L'HEURE
# ====================================================
@app.post("/appel_heure", response_class=PlainTextResponse)
async def appel_heure(SpeechResult: str = Form(None)):
    vr = VoiceResponse()
    message = SpeechResult or ""
    if est_fin_conversation(message):
        audio = tts_voice(END_CALL_MESSAGE)
        vr.play(f"{BASE_URL}/{audio}")
        vr.hangup()
        return str(vr)
    changement = extraire_changement_prestation(message)
    if changement:
        app.state.prestation_tmp = changement
        app.state.coupe_detail_tmp = None
        app.state.couleur_detail_tmp = None
        app.state.gros_changement_tmp = detecter_gros_changement(message)
        app.state.shampoing_tmp = None
        audio = tts_voice("D'accord. Souhaitez-vous une coupe, une couleur, un brushing, une permanente, une mise en plis, un lissage ou un soin ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_prestation")
        return str(vr)
    _, heure, creneau_message, heure_defaut_message = extraire_jour_heure(message, date_du_jour())
    if not heure:
        creneau = creneau_message or getattr(app.state, "creneau_tmp", None)
        heure_defaut = heure_defaut_message or getattr(app.state, "heure_defaut_tmp", None)
        if heure_defaut:
            heure = heure_defaut
            if creneau:
                audio_info = tts_voice(f"Parfait, je comprends {creneau}. Je note {heure}.")
                vr.play(f"{BASE_URL}/{audio_info}")
        else:
            audio = tts_voice("Désolé, je n'ai pas compris l'horaire. Pouvez-vous répéter avec un format comme 15:30, 9h, ou un moment comme matin ?")
            vr.play(f"{BASE_URL}/{audio}")
            vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
            return str(vr)
    question_prefix = ""
    if re.search(r"[?]$", message.strip()) or "c est bon" in normaliser_texte(message) or "cest bon" in normaliser_texte(message):
        question_prefix = f"Oui, bien sûr. Je note {heure}. "
    confirmation_prefix = question_prefix or (f"Oui, bien sûr. Je note {heure}. " if not (creneau_message or heure_defaut_message) and heure else "")
    if not est_horaire_ouverture(heure):
        audio = tts_voice(f"Cette heure est en dehors des horaires d'ouverture ({HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}). Pouvez-vous proposer une autre heure ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
        return str(vr)
    jour = app.state.jour_tmp
    if not app.state.type_tmp:
        app.state.heure_tmp = heure
        audio = tts_voice(f"{confirmation_prefix}Merci. Est-ce pour une prestation homme ou femme ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_type")
        return str(vr)
    if app.state.type_tmp == "femme" and "coupe" in app.state.prestation_tmp and not app.state.coupe_detail_tmp:
        app.state.heure_tmp = heure
        audio = tts_voice(f"{confirmation_prefix}Merci. Quel type de coupe souhaitez-vous ? Exemples : brushing, carré, frange, dégradé.")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_coupe")
        return str(vr)
    if app.state.type_tmp == "femme" and "couleur" in app.state.prestation_tmp and not app.state.couleur_detail_tmp:
        app.state.heure_tmp = heure
        audio = tts_voice(f"{confirmation_prefix}Merci. Quel type de couleur souhaitez-vous ? Exemples : classique, décoloration, mèches, balayage, ombré hair, ton sur ton.")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_couleur")
        return str(vr)
    if app.state.type_tmp == "homme" and "coupe" in app.state.prestation_tmp and not app.state.coupe_detail_tmp:
        app.state.heure_tmp = heure
        audio = tts_voice(f"{confirmation_prefix}Pour une coupe homme, est-ce une coupe normale (dégradé/taper) ou une coupe travaillée avec du volume ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_coupe")
        return str(vr)
    if app.state.type_tmp == "homme" and "couleur" in app.state.prestation_tmp and not app.state.couleur_detail_tmp:
        app.state.heure_tmp = heure
        audio = tts_voice(f"{confirmation_prefix}Pour la couleur homme, est-ce une coloration classique, une décoloration, des mèches/balayage, une couleur fantaisie, ou une patine ton sur ton ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_detail_couleur")
        return str(vr)
    if not app.state.prestation_tmp:
        app.state.heure_tmp = heure
        audio = tts_voice(f"{confirmation_prefix}Merci. Souhaitez-vous une coupe, une couleur, un brushing, une permanente, une mise en plis, un lissage ou un soin ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_prestation")
        return str(vr)
    if app.state.shampoing_tmp is None:
        app.state.heure_tmp = heure
        audio = tts_voice(f"{confirmation_prefix}Souhaitez-vous un shampoing ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_shampoing")
        return str(vr)
    if not est_creneau_disponible(jour, heure):
        audio = tts_voice(f"Le créneau du {jour} à {heure} est déjà pris. Pouvez-vous proposer une autre heure s'il vous plaît ?")
        vr.play(f"{BASE_URL}/{audio}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel_heure")
        return str(vr)
    coupe_min, coupe_max, couleur_min, couleur_max, duree_min, duree_max = calculer_duree_details(
        app.state.type_tmp, app.state.prestation_tmp,
        app.state.coupe_detail_tmp, app.state.couleur_detail_tmp, app.state.gros_changement_tmp,
    )
    if app.state.shampoing_tmp:
        duree_min += 10
        duree_max += 10
    fin_estimee = ajouter_minutes_hhmm(heure, duree_max)
    coupe_prix, couleur_prix, prix_total = calculer_prix_details(
        app.state.type_tmp, app.state.prestation_tmp,
        app.state.coupe_detail_tmp, app.state.couleur_detail_tmp
    )
    enregistrer_rdv(
        app.state.client_id_tmp, jour, heure,
        app.state.type_tmp, app.state.prestation_tmp,
        app.state.coupe_detail_tmp, app.state.couleur_detail_tmp,
        duree_max, prix_total, app.state.shampoing_tmp
    )
    if coupe_min is not None and couleur_min is not None:
        detail_duree = (
            f"{format_plage_simple(coupe_min, coupe_max)} pour la coupe + "
            f"{format_plage_simple(couleur_min, couleur_max)} pour la couleur, "
            f"donc pour le tout {format_plage_simple(duree_min, duree_max)}."
        )
    else:
        detail_duree = f"{format_plage_simple(duree_min, duree_max)}."
    final_msg = (
        f"{confirmation_prefix}Parfait ! Votre rendez-vous est bien prévu pour le {jour} à {heure}. "
        f"Durée estimée {detail_duree} Fin prévue vers {fin_estimee}. "
        f"Vous recevrez la confirmation sur le site : {SITE_CLIENT}. Merci pour votre appel et à bientôt !"
    )
    audio = tts_voice(final_msg)
    vr.play(f"{BASE_URL}/{audio}")
    vr.hangup()
    reset_state()
    return str(vr)


# ====================================================
# SERVIR LES FICHIERS AUDIO
# ====================================================
@app.get("/audio/{filename}")
async def get_audio(filename: str):
    return FileResponse(f"audio/{filename}", media_type="audio/mpeg")


# ====================================================
# MODE CONSOLE (test sans Twilio)
# ====================================================
def mode_console():
    print(f"AGENT : Bonjour, test console activé pour {NOM_SALON}. Tapez 'stop' pour quitter.")

    # Simulation client console
    client = get_or_create_client("console_test")
    nom = client.get("nom")
    client_id = client.get("id")

    if not nom:
        print(f"AGENT : Bonjour, vous êtes bien au {NOM_SALON}. Pourriez-vous me donner votre prénom et votre nom s'il vous plaît ?")
        nom_saisi = input("CLIENT : ").strip()
        if nom_saisi:
            mettre_a_jour_nom_client(client_id, nom_saisi)
            nom = nom_saisi
            print(f"AGENT : Merci {nom}. Comment puis-je vous aider ?")
        else:
            print(f"AGENT : {presentation()}")
    else:
        print(f"AGENT : {presentation(nom)}")

    jour_tmp = None
    creneau_tmp = None
    heure_defaut_tmp = None
    heure_tmp = None
    type_tmp = None
    prestation_tmp = None
    duree_min_tmp = None
    duree_max_tmp = None
    coupe_detail_tmp = None
    couleur_detail_tmp = None
    gros_changement_tmp = False
    shampoing_tmp = None
    etape = "idle"

    while True:
        message = input("CLIENT : ").strip()
        if not message:
            continue
        if message.lower() == "stop":
            print(f"AGENT : {END_CALL_MESSAGE}")
            break
        if etape != "attente_shampoing" and est_fin_conversation(message):
            print(f"AGENT : {END_CALL_MESSAGE}")
            break

        changement = extraire_changement_prestation(message)
        if changement:
            prestation_tmp = changement
            coupe_detail_tmp = None
            couleur_detail_tmp = None
            gros_changement_tmp = detecter_gros_changement(message)
            shampoing_tmp = None
            if not type_tmp:
                etape = "attente_type"
                print("AGENT : D'accord. Est-ce pour une prestation homme ou femme ?")
            else:
                etape = "attente_prestation"
                print("AGENT : D'accord. Souhaitez-vous une coupe, une couleur, un brushing, une permanente, une mise en plis, un lissage ou un soin ?")
            continue

        if etape == "attente_date":
            jour, heure, creneau, heure_par_defaut = extraire_jour_heure(message, date_du_jour())
            if not jour:
                print("AGENT : Je n'ai pas compris le jour. Donnez par exemple 'demain', 'mardi prochain' ou le 10 avril.")
                continue
            if not est_jour_ouvrable(jour):
                print("AGENT : Le salon est ouvert du mardi au samedi. Proposez un autre jour.")
                continue
            if heure:
                if not est_horaire_ouverture(heure):
                    jour_tmp = jour
                    etape = "attente_heure"
                    print(f"AGENT : Nous sommes ouverts de {HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}. Donnez une autre heure.")
                    continue
                if not est_creneau_disponible(jour, heure):
                    jour_tmp = jour
                    etape = "attente_heure"
                    print(f"AGENT : Le créneau du {jour} à {heure} est déjà pris. Donnez une autre heure.")
                    continue
                jour_tmp = jour
                heure_tmp = heure
                print(f"AGENT : Oui, bien sûr. Je note {heure}.")
            jour_tmp = jour
            creneau_tmp = creneau
            heure_defaut_tmp = heure_par_defaut
            etape = "attente_type"
            print("AGENT : Très bien. Est-ce pour une prestation homme ou femme ?")
            continue

        if etape == "attente_type":
            type_prestation = extraire_type_prestation(message)
            if not type_prestation:
                print("AGENT : Pouvez-vous préciser homme ou femme, s'il vous plaît ?")
                continue
            type_tmp = type_prestation
            etape = "attente_prestation"
            print("AGENT : Souhaitez-vous une coupe, une couleur, un brushing, une permanente, une mise en plis, un lissage ou un soin ?")
            continue

        if etape == "attente_prestation":
            prestation = extraire_prestation(message)
            if not prestation:
                print("AGENT : Pouvez-vous préciser: coupe, couleur, ou les deux ?")
                continue
            prestation_tmp = prestation
            if detecter_gros_changement(message):
                gros_changement_tmp = True
            duree_min_tmp, duree_max_tmp = calculer_duree_plage(type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp, gros_changement_tmp)
            if type_tmp == "femme" and "coupe" in prestation_tmp and not coupe_detail_tmp:
                etape = "attente_detail_coupe"
                print("AGENT : Quel type de coupe souhaitez-vous ? Exemples : brushing, carré, frange, dégradé.")
                continue
            if type_tmp == "femme" and "couleur" in prestation_tmp and not couleur_detail_tmp:
                etape = "attente_detail_couleur"
                print("AGENT : Quel type de couleur souhaitez-vous ? Exemples : classique, décoloration, mèches, balayage, ombré hair, ton sur ton.")
                continue
            if heure_tmp:
                if shampoing_tmp is None:
                    etape = "attente_shampoing"
                    print("AGENT : Souhaitez-vous un shampoing ? (oui/non)")
                    continue
                if not est_creneau_disponible(jour_tmp, heure_tmp):
                    print(f"AGENT : Le créneau du {jour_tmp} à {heure_tmp} est déjà pris. Donnez une autre heure.")
                    etape = "attente_heure"
                    continue
                coupe_min, coupe_max, couleur_min, couleur_max, duree_min, duree_max = calculer_duree_details(type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp, gros_changement_tmp)
                if shampoing_tmp:
                    duree_min += 10
                    duree_max += 10
                fin_estimee = ajouter_minutes_hhmm(heure_tmp, duree_max)
                coupe_prix, couleur_prix, prix_total = calculer_prix_details(type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp)
                enregistrer_rdv(client_id, jour_tmp, heure_tmp, type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp, duree_max, prix_total, shampoing_tmp)
                if coupe_min is not None and couleur_min is not None:
                    detail_duree = f"{format_plage_simple(coupe_min, coupe_max)} pour la coupe + {format_plage_simple(couleur_min, couleur_max)} pour la couleur, donc pour le tout {format_plage_simple(duree_min, duree_max)}."
                else:
                    detail_duree = f"{format_plage_simple(duree_min, duree_max)}."
                print(f"AGENT : Parfait ! Votre rendez-vous est bien prévu pour le {jour_tmp} à {heure_tmp}. Durée estimée {detail_duree} Fin vers {fin_estimee}.")
                jour_tmp = creneau_tmp = heure_defaut_tmp = heure_tmp = type_tmp = prestation_tmp = None
                duree_min_tmp = duree_max_tmp = coupe_detail_tmp = couleur_detail_tmp = None
                gros_changement_tmp = False
                shampoing_tmp = None
                etape = "idle"
                continue
            etape = "attente_heure"
            if creneau_tmp:
                print(f"AGENT : Très bien, je note {jour_tmp} en {creneau_tmp}. Pouvez-vous donner une heure précise ? (format HH:MM)")
            else:
                print("AGENT : Très bien. À quelle heure souhaitez-vous venir ? (format HH:MM)")
            continue

        if etape == "attente_shampoing":
            _, heure_message, _, _ = extraire_jour_heure(message, date_du_jour())
            shampoing = extraire_shampoing(message)
            if heure_message:
                if not est_horaire_ouverture(heure_message):
                    print(f"AGENT : Cette heure est en dehors des horaires ({HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}).")
                    continue
                heure_tmp = heure_message
                if shampoing is None:
                    print(f"AGENT : D'accord, je note {heure_message}. Souhaitez-vous un shampoing ? (oui/non)")
                    continue
            if shampoing is None:
                print("AGENT : Souhaitez-vous un shampoing ? (oui/non)")
                continue
            shampoing_tmp = shampoing
            if heure_tmp and type_tmp and prestation_tmp:
                coupe_min, coupe_max, couleur_min, couleur_max, duree_min, duree_max = calculer_duree_details(type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp, gros_changement_tmp)
                if shampoing_tmp:
                    duree_min += 10
                    duree_max += 10
                fin_estimee = ajouter_minutes_hhmm(heure_tmp, duree_max)
                coupe_prix, couleur_prix, prix_total = calculer_prix_details(type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp)
                enregistrer_rdv(client_id, jour_tmp, heure_tmp, type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp, duree_max, prix_total, shampoing_tmp)
                if coupe_min is not None and couleur_min is not None:
                    detail_duree = f"{format_plage_simple(coupe_min, coupe_max)} pour la coupe + {format_plage_simple(couleur_min, couleur_max)} pour la couleur, donc pour le tout {format_plage_simple(duree_min, duree_max)}."
                else:
                    detail_duree = f"{format_plage_simple(duree_min, duree_max)}."
                print(f"AGENT : Parfait ! Votre rendez-vous est bien prévu pour le {jour_tmp} à {heure_tmp}. Durée estimée {detail_duree} Fin vers {fin_estimee}.")
                jour_tmp = creneau_tmp = heure_defaut_tmp = heure_tmp = type_tmp = prestation_tmp = None
                duree_min_tmp = duree_max_tmp = coupe_detail_tmp = couleur_detail_tmp = None
                gros_changement_tmp = False
                shampoing_tmp = None
                etape = "idle"
                continue
            etape = "attente_heure"
            print("AGENT : À quelle heure souhaitez-vous venir ? (format HH:MM)")
            continue

        if etape == "attente_detail_coupe":
            coupe_detail_tmp = extraire_detail_coupe_femme(message)
            duree_min_tmp, duree_max_tmp = calculer_duree_plage(type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp, gros_changement_tmp)
            if type_tmp == "femme" and "couleur" in prestation_tmp and not couleur_detail_tmp:
                etape = "attente_detail_couleur"
                print("AGENT : Quel type de couleur souhaitez-vous ? Exemples : classique, décoloration, mèches, balayage, ombré hair, ton sur ton.")
                continue
            etape = "attente_heure" if heure_tmp is None else ("attente_shampoing" if shampoing_tmp is None else "attente_heure")
            if shampoing_tmp is None and heure_tmp:
                print("AGENT : Souhaitez-vous un shampoing ? (oui/non)")
                etape = "attente_shampoing"
            else:
                print("AGENT : À quelle heure souhaitez-vous venir ? (format HH:MM)")
                etape = "attente_heure"
            continue

        if etape == "attente_detail_couleur":
            if detecter_gros_changement(message):
                gros_changement_tmp = True
            if type_tmp == "homme":
                couleur_detail_tmp = extraire_detail_couleur_homme(message)
            else:
                couleur_detail_tmp = extraire_detail_couleur_femme(message)
            duree_min_tmp, duree_max_tmp = calculer_duree_plage(type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp, gros_changement_tmp)
            if type_tmp == "femme" and "coupe" in prestation_tmp and not coupe_detail_tmp:
                etape = "attente_detail_coupe"
                print("AGENT : Quel type de coupe souhaitez-vous ? Exemples : brushing, carré, frange, dégradé.")
                continue
            if shampoing_tmp is None and heure_tmp:
                print("AGENT : Souhaitez-vous un shampoing ? (oui/non)")
                etape = "attente_shampoing"
            else:
                print("AGENT : À quelle heure souhaitez-vous venir ? (format HH:MM)")
                etape = "attente_heure"
            continue

        if etape == "attente_heure":
            _, heure, creneau_message, heure_defaut_message = extraire_jour_heure(message, date_du_jour())
            if not heure:
                creneau_local = creneau_message or creneau_tmp
                heure_defaut_local = heure_defaut_message or heure_defaut_tmp
                if heure_defaut_local:
                    heure = heure_defaut_local
                    if creneau_local:
                        print(f"AGENT : Parfait, je comprends {creneau_local}. Je note {heure}.")
                else:
                    print("AGENT : Je n'ai pas compris l'heure. Donnez par exemple 15:30 ou 9h.")
                    continue
            if not est_horaire_ouverture(heure):
                print(f"AGENT : Cette heure est en dehors des horaires ({HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}).")
                continue
            if not jour_tmp:
                print("AGENT : Merci de redonner le jour du rendez-vous.")
                etape = "attente_date"
                continue
            if not type_tmp:
                heure_tmp = heure
                etape = "attente_type"
                print("AGENT : Merci. Est-ce pour une prestation homme ou femme ?")
                continue
            if not prestation_tmp:
                heure_tmp = heure
                etape = "attente_prestation"
                print("AGENT : Merci. Souhaitez-vous une coupe, une couleur, un brushing, une permanente, une mise en plis, un lissage ou un soin ?")
                continue
            if shampoing_tmp is None:
                heure_tmp = heure
                etape = "attente_shampoing"
                print("AGENT : Souhaitez-vous un shampoing ? (oui/non)")
                continue
            if not est_creneau_disponible(jour_tmp, heure):
                print(f"AGENT : Le créneau du {jour_tmp} à {heure} est déjà pris. Donnez une autre heure.")
                continue
            coupe_min, coupe_max, couleur_min, couleur_max, duree_min, duree_max = calculer_duree_details(type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp, gros_changement_tmp)
            if shampoing_tmp:
                duree_min += 10
                duree_max += 10
            fin_estimee = ajouter_minutes_hhmm(heure, duree_max)
            coupe_prix, couleur_prix, prix_total = calculer_prix_details(type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp)
            enregistrer_rdv(client_id, jour_tmp, heure, type_tmp, prestation_tmp, coupe_detail_tmp, couleur_detail_tmp, duree_max, prix_total, shampoing_tmp)
            if coupe_min is not None and couleur_min is not None:
                detail_duree = f"{format_plage_simple(coupe_min, coupe_max)} pour la coupe + {format_plage_simple(couleur_min, couleur_max)} pour la couleur, donc pour le tout {format_plage_simple(duree_min, duree_max)}."
            else:
                detail_duree = f"{format_plage_simple(duree_min, duree_max)}."
            print(f"AGENT : Parfait ! Votre rendez-vous est bien prévu pour le {jour_tmp} à {heure}. Durée estimée {detail_duree} Fin vers {fin_estimee}.")
            jour_tmp = creneau_tmp = heure_defaut_tmp = heure_tmp = type_tmp = prestation_tmp = None
            duree_min_tmp = duree_max_tmp = coupe_detail_tmp = couleur_detail_tmp = None
            gros_changement_tmp = False
            shampoing_tmp = None
            etape = "idle"
            continue

        intention = comprendre_intention(message)
        if intention == "info_horaires":
            print(f"AGENT : {repondre_info_horaires(message)}")
            continue
        if intention == "info_prix":
            print(f"AGENT : {repondre_info_prix(message)}")
            continue
        if intention == "info_conseil":
            print("AGENT : Bien sûr. Pouvez-vous me laisser votre numéro de téléphone ? Nous vous rappellerons dans les plus brefs délais.")
            while True:
                tel = input("CLIENT (téléphone) : ").strip()
                res = extraire_telephone(tel)
                if res == "INVALID_LENGTH":
                    print("AGENT : Le numéro n'est pas valide. Merci de le redonner.")
                    continue
                if res:
                    print("AGENT : Merci. Nous vous rappellerons dans les plus brefs délais.")
                    break
                print("AGENT : Je n'ai pas bien compris le numéro. Merci de le redonner.")
            continue
        if intention in {"prendre_rdv", "donner_date", "donner_heure"}:
            jour, heure, creneau, heure_par_defaut = extraire_jour_heure(message, date_du_jour())
            if jour and heure and est_jour_ouvrable(jour) and est_horaire_ouverture(heure) and est_creneau_disponible(jour, heure):
                jour_tmp = jour
                heure_tmp = heure
                gros_changement_tmp = False
                shampoing_tmp = None
                etape = "attente_type"
                print("AGENT : Très bien. Est-ce pour une prestation homme ou femme ?")
                continue
            if jour and not est_jour_ouvrable(jour):
                print("AGENT : Le salon est ouvert du mardi au samedi. Proposez un autre jour.")
                etape = "attente_date"
                continue
            if jour:
                jour_tmp = jour
                creneau_tmp = creneau
                heure_defaut_tmp = heure_par_defaut
                gros_changement_tmp = False
                shampoing_tmp = None
                etape = "attente_type"
                print("AGENT : Très bien. Est-ce pour une prestation homme ou femme ?")
                continue
            etape = "attente_date"
            print("AGENT : Oui, bien sûr. Pour quel jour souhaitez-vous venir ? Exemples: 'demain', 'mardi prochain', le 10 avril.")
            continue
        print(f"AGENT : {reponse_ia(message)}")


if __name__ == "__main__":
    mode_console()
