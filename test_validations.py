#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tests unitaires pour Validation 1 (compétence coiffeur) et Validation 2 (jour de repos).
"""

import sys
import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

# ── Mock OpenAI avant l'import du module ─────────────────────────────────────

class MockUsage:
    prompt_tokens = 5
    completion_tokens = 10
    total_tokens = 15

class MockMessage:
    def __init__(self, content):
        self.content = content
        self.tool_calls = None

class MockChoice:
    def __init__(self, content):
        self.message = MockMessage(content)

class MockGPTResponse:
    def __init__(self, content="D'accord. Pour quel jour souhaitez-vous ?"):
        self.choices = [MockChoice(content)]
        self.usage = MockUsage()

# Patcher openai et twilio avant l'import
with patch("openai.OpenAI"), patch("twilio.rest.Client"):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "real_agent",
        os.path.join(os.path.dirname(__file__), "Real_agent_finish.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["real_agent"] = mod
    spec.loader.exec_module(mod)

# ── Helpers ───────────────────────────────────────────────────────────────────

def reset_state(phone):
    """Remet à zéro le contexte et l'historique pour un numéro de test."""
    mod.client_context.pop(phone, None)
    mod.conversation_history.pop(phone, None)

def next_weekday_iso(weekday: int) -> str:
    """Retourne la date ISO du prochain jour de semaine (0=lundi … 6=dimanche)."""
    today = date.today()
    days_ahead = (weekday - today.weekday()) % 7 or 7
    return (today + timedelta(days=days_ahead)).isoformat()

def mock_supabase_client():
    """Retourne un mock supabase qui ne plante pas sur les appels table()."""
    sb = MagicMock()
    # get_or_create_client → pas de client trouvé → crée un nouveau
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    sb.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": "client-test-id", "nom": "Test", "telephone": "+33600000000"}
    ]
    sb.table.return_value.upsert.return_value.execute.return_value.data = [
        {"id": "client-test-id", "nom": "Test", "telephone": "+33600000000"}
    ]
    return sb

# ── Setup commun ──────────────────────────────────────────────────────────────

def setup_module_state():
    """Configure COIFFEURS, PRESTATIONS_SALON, et les variables salon."""
    mod.COIFFEURS = [
        {
            "nom": "Jean",
            "id": "coif-1",
            "specialites": ["Coupe homme", "Barbe", "Dégradé"],
            "jours_repos": ["dimanche", "lundi"],
            "heure_debut": "09:00",
            "heure_fin": "18:00",
        },
        {
            "nom": "Tom",
            "id": "coif-2",
            "specialites": ["Coupe femme", "Coloration", "Mèches"],
            "jours_repos": ["dimanche", "mercredi"],
            "heure_debut": "09:00",
            "heure_fin": "18:00",
        },
    ]
    mod.PRESTATIONS_SALON = [
        {"name": "Coupe homme", "price": 20, "duration_minutes": 30},
        {"name": "Coupe femme", "price": 35, "duration_minutes": 45},
        {"name": "Barbe",       "price": 15, "duration_minutes": 20},
        {"name": "Coloration",  "price": 60, "duration_minutes": 90},
    ]
    mod.NOM_SALON        = "Test Salon"
    mod.TELEPHONE_SALON  = "+33100000000"
    mod.TWILIO_NUMBER    = "+33100000000"
    mod.HORAIRE_OUVERTURE = "09:00"
    mod.HORAIRE_FERMETURE = "18:00"
    mod.JOURS_OUVERTS    = ["mardi", "mercredi", "jeudi", "vendredi", "samedi"]
    mod.BASE_URL         = "https://test.example.com"
    mod.supabase         = mock_supabase_client()
    # Évite les appels Twilio
    mod.twilio_client    = MagicMock()

# ── Tests ─────────────────────────────────────────────────────────────────────

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results = []

def run_test(name, fn):
    try:
        fn()
        results.append((PASS, name))
        print(f"{PASS}  {name}")
    except AssertionError as e:
        results.append((FAIL, name))
        print(f"{FAIL}  {name} — {e}")
    except Exception as e:
        results.append((FAIL, name))
        print(f"{FAIL}  {name} — Exception inattendue : {type(e).__name__}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION 1a — 1 seul coiffeur compétent → assignation automatique
# ─────────────────────────────────────────────────────────────────────────────
def test_valid1_auto_assign():
    """
    Prestation 'coupe homme' → seul Jean a cette compétence.
    Après run_agent, rdv_coiffeur doit être 'Jean' sans que le client l'ait précisé.
    """
    phone = "+33601000001"
    reset_state(phone)
    setup_module_state()

    # Pré-charger la prestation dans le contexte (simuler qu'elle a été extraite au tour précédent)
    mod.update_client_context(phone, rdv_prestation="coupe homme")

    mock_gpt = MagicMock(return_value=MockGPTResponse("Très bien. Pour quel jour souhaitez-vous ?"))
    mod.client_openai.chat.completions.create = mock_gpt

    _ = mod.run_agent("pour vendredi", phone)

    ctx = mod.get_client_context(phone)
    coiffeur_assigne = ctx.get("rdv_coiffeur", "")
    assert coiffeur_assigne == "Jean", (
        f"Attendu 'Jean' assigné automatiquement, obtenu '{coiffeur_assigne}'"
    )

# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION 1b — 2 coiffeurs compétents → aucune assignation automatique
# ─────────────────────────────────────────────────────────────────────────────
def test_valid1_no_autoassign_when_multiple():
    """
    Prestation sans spécialité exclusive → les 2 coiffeurs sont compétents.
    rdv_coiffeur ne doit PAS être assigné automatiquement.
    """
    phone = "+33601000002"
    reset_state(phone)
    setup_module_state()
    # Ajouter une prestation que les deux coiffeurs font
    mod.COIFFEURS[0]["specialites"].append("Soin cheveux")
    mod.COIFFEURS[1]["specialites"].append("Soin cheveux")

    mod.update_client_context(phone, rdv_prestation="soin cheveux")

    mock_gpt = MagicMock(return_value=MockGPTResponse("Avec quel coiffeur souhaitez-vous ?"))
    mod.client_openai.chat.completions.create = mock_gpt

    _ = mod.run_agent("pour vendredi", phone)

    ctx = mod.get_client_context(phone)
    coiffeur_assigne = ctx.get("rdv_coiffeur", "")
    assert coiffeur_assigne == "", (
        f"Attendu aucune assignation auto (2 compétents), obtenu '{coiffeur_assigne}'"
    )

# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION 2a — Coiffeur en repos le jour demandé → injection dans messages
# ─────────────────────────────────────────────────────────────────────────────
def test_valid2_repos_injection():
    """
    Jean est en repos le lundi. Client demande lundi.
    Le message injecté 'SYSTÈME — VALIDATION' doit apparaître dans les messages envoyés à GPT.
    """
    phone = "+33601000003"
    reset_state(phone)
    setup_module_state()

    lundi_iso = next_weekday_iso(0)  # prochain lundi
    mod.update_client_context(phone, rdv_prestation="coupe homme", rdv_coiffeur="Jean", rdv_jour=lundi_iso)

    messages_capturés = []

    def mock_gpt_capture(**kwargs):
        messages_capturés.extend(kwargs.get("messages", []))
        return MockGPTResponse("Jean est en repos le lundi. Souhaitez-vous un autre jour ?")

    mod.client_openai.chat.completions.create = mock_gpt_capture

    _ = mod.run_agent("ok pour lundi alors", phone)

    injection = next(
        (m for m in messages_capturés
         if isinstance(m.get("content"), str) and "SYSTÈME — VALIDATION" in m["content"]),
        None,
    )
    assert injection is not None, (
        "Message 'SYSTÈME — VALIDATION' non trouvé dans les messages envoyés à GPT.\n"
        f"Messages reçus : {[m.get('content','')[:80] for m in messages_capturés]}"
    )
    assert "Jean" in injection["content"], f"Le nom du coiffeur manque dans l'injection : {injection['content']}"
    assert "lundi" in injection["content"].lower(), f"Le jour manque dans l'injection : {injection['content']}"

# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION 2b — Coiffeur disponible le jour demandé → pas d'injection
# ─────────────────────────────────────────────────────────────────────────────
def test_valid2_no_injection_when_available():
    """
    Jean travaille le vendredi. Aucun message SYSTÈME ne doit être injecté.
    """
    phone = "+33601000004"
    reset_state(phone)
    setup_module_state()

    vendredi_iso = next_weekday_iso(4)  # prochain vendredi
    mod.update_client_context(phone, rdv_prestation="coupe homme", rdv_coiffeur="Jean", rdv_jour=vendredi_iso)

    messages_capturés = []

    def mock_gpt_capture(**kwargs):
        messages_capturés.extend(kwargs.get("messages", []))
        return MockGPTResponse("À quelle heure souhaitez-vous venir ?")

    mod.client_openai.chat.completions.create = mock_gpt_capture

    _ = mod.run_agent("vendredi", phone)

    injection = next(
        (m for m in messages_capturés
         if isinstance(m.get("content"), str) and "SYSTÈME — VALIDATION" in m["content"]),
        None,
    )
    assert injection is None, (
        f"Injection inattendue alors que Jean travaille le vendredi : {injection}"
    )

# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION 2c — Coiffeur alternatif mentionné dans l'injection
# ─────────────────────────────────────────────────────────────────────────────
def test_valid2_alternative_coiffeur_in_injection():
    """
    Jean en repos le lundi, Tom disponible le lundi.
    L'injection doit mentionner Tom comme alternative.
    """
    phone = "+33601000005"
    reset_state(phone)
    setup_module_state()
    # Tom ne travaille pas le mercredi mais travaille le lundi
    # Jean ne travaille pas le lundi

    lundi_iso = next_weekday_iso(0)
    mod.update_client_context(phone, rdv_prestation="coupe homme", rdv_coiffeur="Jean", rdv_jour=lundi_iso)

    messages_capturés = []

    def mock_gpt_capture(**kwargs):
        messages_capturés.extend(kwargs.get("messages", []))
        return MockGPTResponse("Jean est en repos. Tom est disponible.")

    mod.client_openai.chat.completions.create = mock_gpt_capture

    _ = mod.run_agent("lundi", phone)

    injection = next(
        (m for m in messages_capturés
         if isinstance(m.get("content"), str) and "SYSTÈME — VALIDATION" in m["content"]),
        None,
    )
    assert injection is not None, "Injection absente"
    assert "Tom" in injection["content"], (
        f"Tom (coiffeur alternatif) absent de l'injection : {injection['content']}"
    )

# ── Lancement ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 65)
    print("TESTS VALIDATION 1 & 2 — Real_agent_finish.py")
    print("=" * 65 + "\n")

    run_test("V1a — 1 coiffeur compétent → assignation auto",          test_valid1_auto_assign)
    run_test("V1b — 2 coiffeurs compétents → pas d'assignation auto",  test_valid1_no_autoassign_when_multiple)
    run_test("V2a — Coiffeur en repos → injection SYSTÈME dans GPT",   test_valid2_repos_injection)
    run_test("V2b — Coiffeur disponible → pas d'injection",            test_valid2_no_injection_when_available)
    run_test("V2c — Coiffeur alternatif mentionné dans l'injection",   test_valid2_alternative_coiffeur_in_injection)

    print("\n" + "=" * 65)
    passed = sum(1 for r in results if r[0] == PASS)
    print(f"Résultat : {passed}/{len(results)} tests passés")
    print("=" * 65 + "\n")
    sys.exit(0 if passed == len(results) else 1)
