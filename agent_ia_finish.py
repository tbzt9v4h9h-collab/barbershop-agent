# ====================================================
# AGENT IA COIFFEUR — ARCHITECTURE LLM / GPT-4o
# Function calling : GPT-4o décide lui-même des actions
# ====================================================

import os
import json
import uuid
import re
import unicodedata
from datetime import datetime, timedelta
from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse
import openai
from dotenv import load_dotenv
from supabase import create_client, Client

dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.py")
load_dotenv(dotenv_path=dotenv_path)

# ====================================================
# ⚙️ CONFIGURATION DU SALON — À PERSONNALISER
# ====================================================

NOM_SALON        = "Chez les fdp du dégradé"           # À PERSONNALISER
TELEPHONE_SALON  = "01 23 45 67 89"                    # À PERSONNALISER
ADRESSE_SALON    = "12 rue Exemple, 75001 Paris"        # À PERSONNALISER
SITE_CLIENT      = "https://www.monsite-coiffure.com"  # À PERSONNALISER

HORAIRE_OUVERTURE = "09:00"                            # À PERSONNALISER
HORAIRE_FERMETURE = "18:00"                            # À PERSONNALISER
JOURS_OUVERTS = ["mardi", "mercredi", "jeudi", "vendredi", "samedi"]  # À PERSONNALISER

COIFFEURS = [                                          # À PERSONNALISER
    {"nom": "Sophie", "specialites": "coupe femme, couleur"},
    {"nom": "Marc",   "specialites": "coupe homme, barbe"},
]

PRIX_HOMME_COUPE = {                                   # À PERSONNALISER
    "normale":    15,
    "travaillee": 20,
}
PRIX_HOMME_COULEUR = {                                 # À PERSONNALISER
    "classique":          30,
    "decoloration":       40,
    "meches_balayage":    30,
    "fantaisie":          30,
    "patine_ton_sur_ton": 20,
}
PRIX_FEMME_COUPE = {                                   # À PERSONNALISER
    "brushing":       30,
    "carre":          25,
    "frange":         10,
    "degrade":        40,
    "pixie":          35,
    "coupe_courte":   35,
    "longs_naturels": 30,
    "coupe":          30,
}
PRIX_FEMME_COULEUR = {                                 # À PERSONNALISER
    "balayage":    60,
    "meches":      60,
    "ombre_hair":  70,
    "decoloration": 80,
    "ton_sur_ton":  30,
    "couleur":     30,
}

# ====================================================
# CONFIG TECHNIQUE — NE PAS MODIFIER
# ====================================================

openai.api_key = os.getenv("API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

BASE_URL = "https://concealingly-highly-felica.ngrok-free.dev"
END_CALL_MESSAGE = "Merci pour votre appel. Bonne journée et à bientôt au salon."

PRESTATIONS_DUREE = {
    "homme": {
        "coupe":         {"normale": (20, 30), "travaillee": (30, 60)},
        "couleur":       {"classique": (45, 75), "decoloration": (90, 150),
                          "meches_balayage": (90, 120), "fantaisie": (60, 180), "patine_ton_sur_ton": (30, 45)},
    },
    "femme": {
        "coupe":         {"brushing": (45, 60), "carre": (45, 60), "frange": (45, 60),
                          "degrade": (45, 75), "pixie": (30, 45), "coupe_courte": (30, 45),
                          "longs_naturels": (45, 60), "coupe": (30, 45)},
        "couleur_delta": {"balayage": (30, 60), "meches": (30, 60), "ombre_hair": (30, 60),
                          "decoloration": (60, 120), "ton_sur_ton": (-15, 0)},
        "couleur_base":  (90, 120),
    },
}

NOMS_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
NOMS_MOIS  = ["janvier", "février", "mars", "avril", "mai", "juin",
               "juillet", "août", "septembre", "octobre", "novembre", "décembre"]

app = FastAPI()

# État minimal par appel
app.state.conversation_history = []
app.state.client_id    = None
app.state.client_nom   = None
app.state.client_nouveau = False
app.state.telephone    = None
app.state.salon_id     = None
app.state.call_ending  = False
# Prix dynamiques (chargés depuis Supabase si salon identifié)
app.state.prix_homme_coupe   = None
app.state.prix_homme_couleur = None
app.state.prix_femme_coupe   = None
app.state.prix_femme_couleur = None


# ====================================================
# SUPABASE — FONCTIONS CLIENT & RDV
# ====================================================

def get_or_create_client(telephone: str) -> dict:
    try:
        result = supabase.table("clients").select("*").eq("telephone", telephone).execute()
        if result.data:
            client = result.data[0]
            rdv = supabase.table("rendez_vous")\
                .select("prestation, jour").eq("client_id", client["id"])\
                .eq("statut", "confirme").order("jour", desc=True).limit(1).execute()
            client["derniere_prestation"] = rdv.data[0].get("prestation") if rdv.data else None
            client["derniere_date"]       = rdv.data[0].get("jour")       if rdv.data else None
            return client
        nouveau = supabase.table("clients").insert({"telephone": telephone}).execute()
        c = nouveau.data[0]
        c["derniere_prestation"] = None
        c["derniere_date"] = None
        return c
    except Exception as e:
        print(f"Erreur get_or_create_client: {e}")
        return {"id": None, "telephone": telephone, "nom": None, "nb_visites": 0,
                "derniere_prestation": None, "derniere_date": None}


def mettre_a_jour_nom_client(client_id: str, nom: str):
    try:
        supabase.table("clients").update({"nom": nom}).eq("id", client_id).execute()
    except Exception as e:
        print(f"Erreur mettre_a_jour_nom_client: {e}")


def enregistrer_rdv(client_id, jour, heure, type_client, prestation,
                    coupe_detail, couleur_detail, duree_max, prix, avec_shampoing=False):
    try:
        heure_fin = ajouter_minutes(heure, duree_max)
        supabase.table("rendez_vous").insert({
            "client_id": client_id, "jour": jour, "heure_debut": heure,
            "heure_fin": heure_fin, "prestation": prestation, "type_client": type_client,
            "coupe_detail": coupe_detail, "couleur_detail": couleur_detail,
            "avec_shampoing": avec_shampoing, "prix": prix, "statut": "confirme",
        }).execute()
        if client_id:
            row = supabase.table("clients").select("nb_visites").eq("id", client_id).execute().data
            if row:
                supabase.table("clients").update({"nb_visites": row[0]["nb_visites"] + 1})\
                    .eq("id", client_id).execute()
    except Exception as e:
        print(f"Erreur enregistrer_rdv: {e}")


def annuler_rdv_db(rdv_id: str, client_id: str) -> bool:
    try:
        supabase.table("rendez_vous").update({"statut": "annule"})\
            .eq("id", rdv_id).eq("client_id", client_id).execute()
        return True
    except Exception as e:
        print(f"Erreur annuler_rdv_db: {e}")
        return False


def modifier_rdv_db(rdv_id: str, client_id: str, nouveau_jour: str, nouvelle_heure: str) -> bool:
    try:
        supabase.table("rendez_vous").update({
            "jour": nouveau_jour,
            "heure_debut": nouvelle_heure,
            "heure_fin": ajouter_minutes(nouvelle_heure, 30),
        }).eq("id", rdv_id).eq("client_id", client_id).execute()
        return True
    except Exception as e:
        print(f"Erreur modifier_rdv_db: {e}")
        return False


def est_creneau_disponible(jour: str, heure: str) -> bool:
    try:
        result = supabase.table("rendez_vous").select("id")\
            .eq("jour", jour).eq("heure_debut", heure).eq("statut", "confirme").execute()
        return len(result.data) == 0
    except Exception as e:
        print(f"Erreur est_creneau_disponible: {e}")
        return True


def get_rdv_client(client_id: str) -> list:
    try:
        today = datetime.now().date().isoformat()
        result = supabase.table("rendez_vous").select("*")\
            .eq("client_id", client_id).eq("statut", "confirme")\
            .gte("jour", today).order("jour").execute()
        return result.data or []
    except Exception as e:
        print(f"Erreur get_rdv_client: {e}")
        return []


# ====================================================
# BASE44 — INTÉGRATION MULTI-SALON
# ====================================================

def get_salon_by_twilio(twilio_number: str) -> dict | None:
    try:
        result = supabase.table("Salon").select("*")\
            .eq("twilio_number", twilio_number).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"Erreur get_salon_by_twilio: {e}")
        return None


def get_services_from_base44(salon_id: str) -> list:
    try:
        result = supabase.table("Service")\
            .select("name, price, duration_minutes, category").eq("salon_id", salon_id).execute()
        return result.data or []
    except Exception as e:
        print(f"Erreur get_services_from_base44: {e}")
        return []


def get_employees_from_base44(salon_id: str) -> list:
    try:
        result = supabase.table("Employee")\
            .select("full_name, specialties, work_start, work_end, working_days")\
            .eq("salon_id", salon_id).eq("is_active", True).execute()
        return result.data or []
    except Exception as e:
        print(f"Erreur get_employees_from_base44: {e}")
        return []


def load_prix_from_base44(salon_id: str):
    services = get_services_from_base44(salon_id)
    if not services:
        return
    hc, hcol, fc, fcol = {}, {}, {}, {}
    for svc in services:
        name  = (svc.get("name") or "").lower().replace(" ", "_").replace("-", "_")
        price = svc.get("price") or 0
        cat   = (svc.get("category") or "").lower()
        if "homme" in cat and "coupe" in cat:    hc[name]   = price
        elif "homme" in cat and "couleur" in cat: hcol[name] = price
        elif "femme" in cat and "coupe" in cat:   fc[name]   = price
        elif "femme" in cat and "couleur" in cat: fcol[name] = price
    if hc:   app.state.prix_homme_coupe   = hc
    if hcol: app.state.prix_homme_couleur = hcol
    if fc:   app.state.prix_femme_coupe   = fc
    if fcol: app.state.prix_femme_couleur = fcol


def sync_rdv_to_base44(rdv_data: dict):
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
# TTS — VOIX NATURELLE
# ====================================================

def tts_voice(message: str) -> str:
    audio_id = str(uuid.uuid4()) + ".mp3"
    path = f"audio/{audio_id}"
    os.makedirs("audio", exist_ok=True)
    with open(path, "wb") as f:
        result = openai.audio.speech.create(model="gpt-4o-mini-tts", voice="alloy", input=message)
        f.write(result.read() if hasattr(result, "read") else bytes(result))
    return path


# ====================================================
# UTILITAIRES DATE / HEURE / CALCULS
# ====================================================

def ajouter_minutes(hhmm: str, minutes: int) -> str:
    h, m = hhmm.split(":")
    total = (int(h) * 60 + int(m) + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def fmt_duree(minutes: int) -> str:
    if minutes >= 60:
        h, m = minutes // 60, minutes % 60
        return f"{h}h{m:02d}" if m else f"{h}h"
    return f"{minutes} min"


def calculer_duree(type_client: str, prestation: str,
                   coupe_detail: str | None, couleur_detail: str | None,
                   gros_changement: bool = False) -> tuple[int, int]:
    """Retourne (duree_min, duree_max) en minutes."""
    if prestation == "brushing":     return (45, 60)
    if prestation == "permanente":   return (120, 120)
    if prestation == "mise_en_plis": return (60, 60)
    if prestation == "lissage":      return (45, 45)
    if prestation == "soin":         return (30, 30)
    if prestation == "lissage_soin": return (75, 75)

    if type_client == "homme":
        coupe_map   = PRESTATIONS_DUREE["homme"]["coupe"]
        couleur_map = PRESTATIONS_DUREE["homme"]["couleur"]
        cd = coupe_detail or "normale"
        cold = couleur_detail or "classique"
        cm = coupe_map.get(cd, (20, 30))
        colm = couleur_map.get(cold, (45, 75))
        if prestation == "coupe":         return cm
        if prestation == "couleur":       return colm
        return (cm[0] + colm[0], cm[1] + colm[1])

    coupe_map   = PRESTATIONS_DUREE["femme"]["coupe"]
    base        = PRESTATIONS_DUREE["femme"]["couleur_base"]
    delta_map   = PRESTATIONS_DUREE["femme"]["couleur_delta"]
    cd  = coupe_detail  or "coupe"
    cold = couleur_detail or "couleur"
    cm  = coupe_map.get(cd, (30, 45))
    d   = delta_map.get(cold, (0, 0))
    colm = (base[0] + d[0] + (30 if gros_changement else 0),
            base[1] + d[1] + (40 if gros_changement else 0))
    if prestation == "coupe":   return cm
    if prestation == "couleur": return colm
    return (cm[0] + colm[0], cm[1] + colm[1])


def calculer_prix(type_client: str, prestation: str,
                  coupe_detail: str | None, couleur_detail: str | None) -> int:
    ph_c  = app.state.prix_homme_coupe   or PRIX_HOMME_COUPE
    ph_cl = app.state.prix_homme_couleur or PRIX_HOMME_COULEUR
    pf_c  = app.state.prix_femme_coupe   or PRIX_FEMME_COUPE
    pf_cl = app.state.prix_femme_couleur or PRIX_FEMME_COULEUR

    if type_client == "homme":
        cd, cold = coupe_detail or "normale", couleur_detail or "classique"
        p = 0
        if prestation in {"coupe", "coupe_couleur"}:   p += ph_c.get(cd, 15)
        if prestation in {"couleur", "coupe_couleur"}: p += ph_cl.get(cold, 30)
        return p
    cd, cold = coupe_detail or "coupe", couleur_detail or "couleur"
    p = 0
    if prestation in {"coupe", "coupe_couleur"}:   p += pf_c.get(cd, 30)
    if prestation in {"couleur", "coupe_couleur"}: p += pf_cl.get(cold, 30)
    return p


def format_date_longue(date_obj) -> str:
    return f"{NOMS_JOURS[date_obj.weekday()]} {date_obj.day} {NOMS_MOIS[date_obj.month - 1]} {date_obj.year}"


# ====================================================
# GPT-4o AGENT — TOOLS + FUNCTION CALLING
# ====================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "verifier_disponibilite",
            "description": "Vérifie si un créneau est disponible dans le planning du salon.",
            "parameters": {
                "type": "object",
                "properties": {
                    "jour":  {"type": "string", "description": "Date au format YYYY-MM-DD"},
                    "heure": {"type": "string", "description": "Heure au format HH:MM"},
                },
                "required": ["jour", "heure"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prendre_rdv",
            "description": "Enregistre un rendez-vous confirmé. Appeler seulement après avoir vérifié la dispo et obtenu toutes les infos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "jour":           {"type": "string", "description": "Date YYYY-MM-DD"},
                    "heure":          {"type": "string", "description": "Heure HH:MM"},
                    "type_client":    {"type": "string", "enum": ["homme", "femme"]},
                    "prestation":     {"type": "string", "description": "coupe | couleur | coupe_couleur | brushing | permanente | mise_en_plis | lissage | soin"},
                    "coupe_detail":   {"type": "string", "description": "Détail de la coupe (ex: normale, travaillee, carre, frange, degrade, pixie)"},
                    "couleur_detail": {"type": "string", "description": "Détail de la couleur (ex: classique, decoloration, meches_balayage, balayage, ton_sur_ton)"},
                    "avec_shampoing": {"type": "boolean", "description": "Client veut un shampoing"},
                    "gros_changement":{"type": "boolean", "description": "Gros changement de couleur (allonge la durée)"},
                },
                "required": ["jour", "heure", "type_client", "prestation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "annuler_rdv",
            "description": "Annule un rendez-vous existant du client.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rdv_id": {"type": "string", "description": "ID du RDV à annuler"},
                },
                "required": ["rdv_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modifier_rdv",
            "description": "Modifie la date et l'heure d'un rendez-vous existant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rdv_id":         {"type": "string", "description": "ID du RDV à modifier"},
                    "nouveau_jour":   {"type": "string", "description": "Nouvelle date YYYY-MM-DD"},
                    "nouvelle_heure": {"type": "string", "description": "Nouvelle heure HH:MM"},
                },
                "required": ["rdv_id", "nouveau_jour", "nouvelle_heure"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_services",
            "description": "Retourne les prestations, tarifs et horaires du salon.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_client_info",
            "description": "Retourne les informations du client et ses prochains rendez-vous.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_coiffeurs",
            "description": "Retourne la liste des coiffeurs et leurs spécialités.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_client_name",
            "description": "Enregistre le prénom du client dans la base de données.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nom": {"type": "string", "description": "Prénom (et nom) du client"},
                },
                "required": ["nom"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminer_appel",
            "description": "Termine la conversation et raccroche. Appeler quand le client dit au revoir ou que tout est réglé.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def build_system_prompt(ctx: dict) -> str:
    today = datetime.now()
    jours_ouverts_str = ", ".join(JOURS_OUVERTS)
    coiffeurs_str = "\n".join(f"- {c['nom']} : {c['specialites']}" for c in COIFFEURS)
    ph_c  = app.state.prix_homme_coupe   or PRIX_HOMME_COUPE
    ph_cl = app.state.prix_homme_couleur or PRIX_HOMME_COULEUR
    pf_c  = app.state.prix_femme_coupe   or PRIX_FEMME_COUPE
    pf_cl = app.state.prix_femme_couleur or PRIX_FEMME_COULEUR

    client_info = ""
    if ctx.get("client_nom"):
        client_info = f"\nCLIENT EN LIGNE : {ctx['client_nom']} (client connu)"
    elif ctx.get("client_nouveau"):
        client_info = "\nCLIENT EN LIGNE : nouveau client (prénom inconnu)"

    return f"""Tu es un agent vocal IA pour le salon de coiffure "{NOM_SALON}".
Tu réponds au téléphone. Sois chaleureux, naturel et concis.
Tes réponses sont lues à voix haute : pas de listes à puces, pas de caractères spéciaux, phrases courtes.
{client_info}

INFORMATIONS DU SALON :
- Adresse : {ADRESSE_SALON}
- Téléphone : {TELEPHONE_SALON}
- Site : {SITE_CLIENT}
- Horaires : {HORAIRE_OUVERTURE} à {HORAIRE_FERMETURE}
- Jours ouverts : {jours_ouverts_str}
- Aujourd'hui : {format_date_longue(today.date())} ({today.strftime("%Y-%m-%d")})

ÉQUIPE :
{coiffeurs_str}

TARIFS (euros) :
  Homme coupe : {json.dumps(ph_c, ensure_ascii=False)}
  Homme couleur : {json.dumps(ph_cl, ensure_ascii=False)}
  Femme coupe : {json.dumps(pf_c, ensure_ascii=False)}
  Femme couleur : {json.dumps(pf_cl, ensure_ascii=False)}

RÈGLES :
1. Pour prendre un RDV, collecte : jour, heure, homme/femme, prestation, détails coupe/couleur.
2. Vérifie toujours la dispo avant de confirmer.
3. Si créneau indisponible, propose une alternative.
4. Si c'est un nouveau client et qu'un RDV est confirmé, demande son prénom puis appelle save_client_name.
5. Appelle terminer_appel quand le client dit au revoir ou que tout est réglé.
6. Réponds toujours en français, 2-3 phrases max par réponse."""


def execute_tool(name: str, args: dict, ctx: dict) -> dict:
    client_id = ctx.get("client_id")
    salon_id  = ctx.get("salon_id")

    if name == "verifier_disponibilite":
        dispo = est_creneau_disponible(args["jour"], args["heure"])
        return {"disponible": dispo, "jour": args["jour"], "heure": args["heure"]}

    if name == "prendre_rdv":
        jour     = args["jour"]
        heure    = args["heure"]
        type_cl  = args["type_client"]
        presta   = args["prestation"]
        cd       = args.get("coupe_detail")
        cold     = args.get("couleur_detail")
        shamp    = args.get("avec_shampoing", False)
        gros     = args.get("gros_changement", False)

        if not est_creneau_disponible(jour, heure):
            return {"success": False, "error": "Créneau non disponible, propose une autre heure."}

        duree_min, duree_max = calculer_duree(type_cl, presta, cd, cold, gros)
        if shamp:
            duree_min += 10
            duree_max += 10
        prix_total = calculer_prix(type_cl, presta, cd, cold)
        heure_fin  = ajouter_minutes(heure, duree_max)

        enregistrer_rdv(client_id, jour, heure, type_cl, presta, cd, cold, duree_max, prix_total, shamp)
        sync_rdv_to_base44({
            "salon_id":     salon_id,
            "client_name":  ctx.get("client_nom"),
            "client_phone": ctx.get("telephone"),
            "employee_id":  None,
            "service_id":   None,
            "date":         jour,
            "time":         heure,
        })
        return {
            "success":   True,
            "jour":      jour,
            "heure":     heure,
            "heure_fin": heure_fin,
            "prestation": presta,
            "prix":      prix_total,
            "duree":     f"{fmt_duree(duree_min)} à {fmt_duree(duree_max)}",
            "site_confirmation": SITE_CLIENT,
        }

    if name == "annuler_rdv":
        ok = annuler_rdv_db(args["rdv_id"], client_id)
        return {"success": ok}

    if name == "modifier_rdv":
        nj, nh = args["nouveau_jour"], args["nouvelle_heure"]
        if not est_creneau_disponible(nj, nh):
            return {"success": False, "error": "Nouveau créneau non disponible."}
        ok = modifier_rdv_db(args["rdv_id"], client_id, nj, nh)
        return {"success": ok, "nouveau_jour": nj, "nouvelle_heure": nh}

    if name == "get_services":
        return {
            "homme": {"coupes": app.state.prix_homme_coupe or PRIX_HOMME_COUPE,
                      "couleurs": app.state.prix_homme_couleur or PRIX_HOMME_COULEUR},
            "femme": {"coupes": app.state.prix_femme_coupe or PRIX_FEMME_COUPE,
                      "couleurs": app.state.prix_femme_couleur or PRIX_FEMME_COULEUR},
            "horaires": f"{HORAIRE_OUVERTURE} - {HORAIRE_FERMETURE}",
            "jours_ouverts": JOURS_OUVERTS,
        }

    if name == "get_client_info":
        rdvs = get_rdv_client(client_id) if client_id else []
        return {"nom": ctx.get("client_nom"), "rdv_a_venir": rdvs}

    if name == "get_coiffeurs":
        employes = get_employees_from_base44(salon_id) if salon_id else []
        return {"coiffeurs": employes or COIFFEURS}

    if name == "save_client_name":
        nom = args.get("nom", "").strip()
        if client_id and nom:
            mettre_a_jour_nom_client(client_id, nom)
            app.state.client_nom = nom
        return {"success": True, "nom": nom}

    if name == "terminer_appel":
        return {"_hangup": True}

    return {"error": f"Outil inconnu : {name}"}


def run_agent(conversation_history: list, ctx: dict) -> tuple[str, bool]:
    """
    Envoie la conversation à GPT-4o avec les tools.
    Retourne (texte_réponse, doit_raccrocher).
    """
    messages = [{"role": "system", "content": build_system_prompt(ctx)}] + conversation_history[-20:]
    hangup = False

    for _ in range(6):
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            return (msg.content or END_CALL_MESSAGE), hangup

        messages.append(msg)
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            result = execute_tool(tc.function.name, args, ctx)
            if result.get("_hangup"):
                hangup = True
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    return END_CALL_MESSAGE, True


# ====================================================
# GREETING (message initial avant la 1ère parole)
# ====================================================

def message_accueil(nom: str | None, derniere_prestation: str | None, derniere_date: str | None) -> str:
    if nom and derniere_prestation and derniere_date:
        return (
            f"Bonjour {nom}, ravi de vous retrouver ! "
            f"Votre dernière visite était une {derniere_prestation} le {derniere_date}. "
            f"Souhaitez-vous reprendre la même prestation ?"
        )
    if nom:
        return f"Bonjour {nom}, ravi de vous retrouver au {NOM_SALON}. Que puis-je faire pour vous ?"
    return f"Bonjour, vous êtes bien au {NOM_SALON}. Comment puis-je vous aider ?"


def reset_state():
    app.state.conversation_history = []
    app.state.client_id    = None
    app.state.client_nom   = None
    app.state.client_nouveau = False
    app.state.telephone    = None
    app.state.salon_id     = None
    app.state.call_ending  = False
    app.state.prix_homme_coupe   = None
    app.state.prix_homme_couleur = None
    app.state.prix_femme_coupe   = None
    app.state.prix_femme_couleur = None


# ====================================================
# TWILIO — ROUTE PRINCIPALE
# ====================================================

@app.post("/appel", response_class=PlainTextResponse)
async def appel(
    SpeechResult: str = Form(None),
    From: str = Form(None),
    To:   str = Form(None),
):
    vr = VoiceResponse()

    # ── Début de l'appel ──────────────────────────────
    if SpeechResult is None:
        reset_state()

        # Identifier le salon via le numéro Twilio
        salon = get_salon_by_twilio(To) if To else None
        if salon:
            app.state.salon_id = salon.get("id")
            load_prix_from_base44(app.state.salon_id)

        # Identifier le client
        telephone = From or "console_test"
        client = get_or_create_client(telephone)
        app.state.client_id      = client.get("id")
        app.state.client_nom     = client.get("nom")
        app.state.client_nouveau = not bool(client.get("nom"))
        app.state.telephone      = telephone

        msg = message_accueil(
            client.get("nom"),
            client.get("derniere_prestation"),
            client.get("derniere_date"),
        )
        vr.play(url=f"{BASE_URL}/{tts_voice(msg)}")
        vr.gather(input="speech", speechTimeout="auto", action="/appel")
        return str(vr)

    # ── Message du client ─────────────────────────────
    app.state.conversation_history.append({"role": "user", "content": SpeechResult})

    ctx = {
        "client_id":    app.state.client_id,
        "client_nom":   app.state.client_nom,
        "client_nouveau": app.state.client_nouveau,
        "telephone":    app.state.telephone,
        "salon_id":     app.state.salon_id,
    }

    try:
        reponse, hangup = run_agent(app.state.conversation_history, ctx)
    except Exception as e:
        print(f"Erreur GPT-4o: {e}")
        reponse = "Désolé, j'ai un problème technique. Pouvez-vous rappeler dans quelques instants ?"
        hangup = False

    app.state.conversation_history.append({"role": "assistant", "content": reponse})

    vr.play(f"{BASE_URL}/{tts_voice(reponse)}")
    if hangup:
        reset_state()
        vr.hangup()
    else:
        vr.gather(input="speech", speechTimeout="auto", action="/appel")
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
    reset_state()
    client = get_or_create_client("console_test")
    app.state.client_id      = client.get("id")
    app.state.client_nom     = client.get("nom")
    app.state.client_nouveau = not bool(client.get("nom"))
    app.state.telephone      = "console_test"

    ctx = {
        "client_id":    app.state.client_id,
        "client_nom":   app.state.client_nom,
        "client_nouveau": app.state.client_nouveau,
        "telephone":    "console_test",
        "salon_id":     None,
    }

    print(f"AGENT : {message_accueil(client.get('nom'), client.get('derniere_prestation'), client.get('derniere_date'))}")

    while True:
        message = input("CLIENT : ").strip()
        if not message:
            continue
        if message.lower() == "stop":
            print(f"AGENT : {END_CALL_MESSAGE}")
            break

        app.state.conversation_history.append({"role": "user", "content": message})
        try:
            reponse, hangup = run_agent(app.state.conversation_history, ctx)
        except Exception as e:
            reponse, hangup = f"Erreur: {e}", False

        app.state.conversation_history.append({"role": "assistant", "content": reponse})
        print(f"AGENT : {reponse}")

        if hangup:
            break


if __name__ == "__main__":
    mode_console()
