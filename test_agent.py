#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent.py — Tests end-to-end de l'agent barbershop
Envoie de vraies requêtes HTTP à https://barbershop-agent.onrender.com
et vérifie les effets dans Supabase.
"""

import re
import sys
import time
from datetime import date, timedelta

import requests
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL      = "https://barbershop-agent.onrender.com"
FROM_NUMBER   = "+33786730039"
SUPABASE_URL  = "https://sqeqlsjmysbisnxsfuwc.supabase.co"
SUPABASE_KEY  = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNxZXFsc2pteXNiaXNueHNmdXdjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzUyOTcxOTUsImV4cCI6MjA5MDg3MzE5NX0"
    ".zAdKGNDh4s3WIoDpvtHH6gBRj8LKeDwuwVWXedS7HBA"
)

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

PASS = "✅"
FAIL = "❌"
SEP  = "─" * 60

# ── Helpers ───────────────────────────────────────────────────────────────────

def post_appel(call_sid: str, speech: str | None = None,
               call_status: str = "in-progress") -> requests.Response:
    """Envoie une requête POST /appel au format Twilio."""
    payload = {
        "CallSid":     call_sid,
        "From":        FROM_NUMBER,
        "CallStatus":  call_status,
    }
    if speech:
        payload["SpeechResult"] = speech
    return requests.post(
        f"{BASE_URL}/appel",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=45,
    )


def extract_say(twiml: str) -> list[str]:
    """Extrait le texte de toutes les balises <Say>…</Say>."""
    return re.findall(r"<Say[^>]*>(.*?)</Say>", twiml, re.DOTALL | re.IGNORECASE)


def print_step(label: str, resp: requests.Response, checks: list[tuple[bool, str]]):
    say_texts = extract_say(resp.text)
    all_ok    = all(ok for ok, _ in checks)
    icon      = PASS if all_ok else FAIL

    print(f"\n  {icon} {label}  [HTTP {resp.status_code}]")
    for text in say_texts:
        preview = text.strip().replace("\n", " ")[:220]
        print(f"     📢 {preview}")
    for ok, msg in checks:
        print(f"     {'✓' if ok else '✗'} {msg}")


def next_weekday(weekday: int) -> str:
    """Retourne la prochaine date ISO pour le jour de semaine donné (0=lun, …, 6=dim)."""
    today = date.today()
    days_ahead = (weekday - today.weekday()) % 7 or 7
    return (today + timedelta(days=days_ahead)).isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 1 — Pause déjeuner
# ══════════════════════════════════════════════════════════════════════════════

def scenario_1():
    print(f"\n{SEP}")
    print("SCÉNARIO 1 — Pause déjeuner")
    print(f"{SEP}")

    # Étape 1 : accueil
    try:
        r1 = post_appel("TEST001")
        checks1 = [
            (r1.status_code == 200, f"status=200 (reçu {r1.status_code})"),
            (bool(extract_say(r1.text)),     "réponse TwiML contient <Say>"),
        ]
        print_step("Étape 1 — Accueil", r1, checks1)
    except Exception as e:
        print(f"\n  {FAIL} Étape 1 — Erreur réseau : {e}")
        return False

    time.sleep(1)

    # Étape 2 : demande à midi (heure de pause)
    try:
        r2 = post_appel(
            "TEST001",
            speech="je veux une boule à zéro aujourd'hui à midi",
        )
        body_lower = r2.text.lower()
        say_lower  = " ".join(extract_say(r2.text)).lower()
        mentions_pause = any(kw in say_lower for kw in [
            "pause", "12h", "12 h", "ferme", "fermé", "déjeuner",
            "indisponible", "disponible avant", "à partir",
            "repos", "pas disponible", "pas de coiffeur",
            "autre jour", "demain", "quel jour",
        ])
        checks2 = [
            (r2.status_code == 200,  f"status=200 (reçu {r2.status_code})"),
            (bool(extract_say(r2.text)), "réponse TwiML contient <Say>"),
            (mentions_pause,             "mentionne pause / indisponibilité à midi"),
        ]
        print_step("Étape 2 — Boule à zéro à midi", r2, checks2)
        return all(ok for ok, _ in checks2)
    except Exception as e:
        print(f"\n  {FAIL} Étape 2 — Erreur réseau : {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 2 — Fidélité + vérification Supabase
# ══════════════════════════════════════════════════════════════════════════════

def scenario_2():
    print(f"\n{SEP}")
    print("SCÉNARIO 2 — Fidélité (boule à zéro = 45€)")
    print(f"{SEP}")

    jeudi_iso = next_weekday(3)   # prochain jeudi
    print(f"  → Prochain jeudi : {jeudi_iso}")

    # Étape 1 : accueil
    try:
        r1 = post_appel("TEST002")
        checks1 = [
            (r1.status_code == 200, f"status=200 (reçu {r1.status_code})"),
            (bool(extract_say(r1.text)),     "réponse TwiML contient <Say>"),
        ]
        print_step("Étape 1 — Accueil", r1, checks1)
    except Exception as e:
        print(f"\n  {FAIL} Étape 1 — Erreur réseau : {e}")
        return False

    time.sleep(1)

    # Étape 2 : demande de RDV jeudi 10h
    try:
        r2 = post_appel(
            "TEST002",
            speech="je veux une boule à zéro jeudi à 10h",
        )
        say_lower = " ".join(extract_say(r2.text)).lower()
        rdv_confirme = any(kw in say_lower for kw in [
            "confirmé", "confirm", "sms", "enregistré",
            "rendez-vous", "10h", "10 h",
        ])
        checks2 = [
            (r2.status_code == 200,  f"status=200 (reçu {r2.status_code})"),
            (bool(extract_say(r2.text)), "réponse TwiML contient <Say>"),
            (rdv_confirme,               "mention confirmation / RDV dans la réponse"),
        ]
        print_step("Étape 2 — Boule à zéro jeudi 10h", r2, checks2)
    except Exception as e:
        print(f"\n  {FAIL} Étape 2 — Erreur réseau : {e}")
        return False

    # Attente propagation Supabase
    print(f"\n  ⏳ Attente 3 s pour propagation Supabase…")
    time.sleep(3)

    # Vérification call_stats
    print("\n  🔍 Vérification Supabase — call_stats")
    try:
        res_cs = (
            sb.table("call_stats")
            .select("call_sid, rdv_pris, prestation, client_phone")
            .eq("call_sid", "TEST002")
            .limit(1)
            .execute()
        )
        row_cs = res_cs.data[0] if res_cs.data else None
        cs_found  = row_cs is not None
        cs_rdv    = bool(row_cs.get("rdv_pris")) if row_cs else False
        print(f"     {'✓' if cs_found else '✗'} Ligne call_stats TEST002 {'trouvée' if cs_found else 'absente'}")
        if row_cs:
            print(f"       rdv_pris={row_cs.get('rdv_pris')} | "
                  f"prestation={row_cs.get('prestation')} | "
                  f"phone={row_cs.get('client_phone')}")
    except Exception as e:
        cs_found, cs_rdv = False, False
        print(f"     ✗ Erreur lecture call_stats : {e}")

    # Vérification appointment
    print("\n  🔍 Vérification Supabase — appointment")
    try:
        res_ap = (
            sb.table("appointment")
            .select("id, date, time, service, client_phone, status")
            .eq("client_phone", FROM_NUMBER)
            .eq("date", jeudi_iso)
            .limit(5)
            .execute()
        )
        appts = res_ap.data or []
        ap_found = bool(appts)
        print(f"     {'✓' if ap_found else '✗'} RDV appointment jeudi {jeudi_iso} "
              f"{'trouvé' if ap_found else 'absent'}")
        for a in appts:
            print(f"       id={a.get('id')} | {a.get('time')} | "
                  f"{a.get('service')} | statut={a.get('status')}")
    except Exception as e:
        ap_found = False
        print(f"     ✗ Erreur lecture appointment : {e}")

    ok = cs_found and ap_found
    print(f"\n  {PASS if ok else FAIL} Scénario 2 — {'complet' if ok else 'incomplet (voir détails)'}")
    return ok


# ══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 3 — GET /dispos
# ══════════════════════════════════════════════════════════════════════════════

def scenario_3():
    print(f"\n{SEP}")
    print("SCÉNARIO 3 — GET /dispos")
    print(f"{SEP}")

    jeudi_iso = next_weekday(3)
    url = f"{BASE_URL}/dispos?jour={jeudi_iso}"
    print(f"  → {url}")

    try:
        r = requests.get(url, timeout=20)
        body = r.text
        try:
            import json
            data = r.json()
            pretty = json.dumps(data, ensure_ascii=False, indent=2)[:600]
        except Exception:
            pretty = body[:400]

        has_slots = any(kw in body.lower() for kw in [
            "créneau", "creneau", "dispo", "heure", "disponible",
            "09:", "10:", "11:", "14:", "15:", "16:",
        ])
        checks = [
            (r.status_code == 200,   f"status=200 (reçu {r.status_code})"),
            (len(body) > 10,          "réponse non vide"),
            (has_slots,               "réponse contient des créneaux / horaires"),
        ]
        all_ok = all(ok for ok, _ in checks)
        print(f"\n  {PASS if all_ok else FAIL} GET /dispos  [HTTP {r.status_code}]")
        print(f"\n  📋 Réponse :\n{pretty}")
        for ok, msg in checks:
            print(f"     {'✓' if ok else '✗'} {msg}")
        return all_ok
    except Exception as e:
        print(f"\n  {FAIL} Erreur réseau : {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{'═' * 60}")
    print(f"  TESTS END-TO-END — {BASE_URL}")
    print(f"{'═' * 60}")

    results = {}
    results["S1 — Pause déjeuner"]  = scenario_1()
    results["S2 — Fidélité + DB"]   = scenario_2()
    results["S3 — GET /dispos"]     = scenario_3()

    print(f"\n{'═' * 60}")
    print("  RÉSUMÉ")
    print(f"{'═' * 60}")
    for label, ok in results.items():
        print(f"  {PASS if ok else FAIL}  {label}")

    total = len(results)
    passed = sum(1 for ok in results.values() if ok)
    print(f"\n  {passed}/{total} scénarios OK")
    print(f"{'═' * 60}\n")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
