#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script de test du nouvel agent barbershop avec GPT-4o
"""

import sys
import os
import importlib.util

# Charger le module avec le nom contenant des espaces
spec = importlib.util.spec_from_file_location("agent", "agent ia finish.py")
agent_module = importlib.util.module_from_spec(spec)
sys.modules["agent"] = agent_module
spec.loader.exec_module(agent_module)

run_agent = agent_module.run_agent

print("\n" + "="*70)
print("🎤 TEST DES 3 CONVERSATIONS AVEC GPT-4o")
print("="*70 + "\n")

# Test 1: Prendre RDV en une phrase
print("=" * 70)
print("TEST 1: Demande de RDV complexe en une seule phrase")
print("=" * 70)
test_phone_1 = "0612345671"
conversation_1 = [
    "bonjour j'aimerais prendre rdv pour demain pour un homme, une coupe et si possible vers 14h",
]

for i, msg in enumerate(conversation_1, 1):
    print(f"\n👤 Client (msg {i}): {msg}")
    try:
        response = run_agent(msg, test_phone_1)
        print(f"🤖 Agent: {response}")
    except Exception as e:
        print(f"❌ Erreur: {e}")

# Test 2: Annulation de RDV
print("\n\n" + "=" * 70)
print("TEST 2: Annulation de rendez-vous")
print("=" * 70)
test_phone_2 = "0612345672"
conversation_2 = [
    "salut, c'est Thomas",
    "je voudrais annuler mon rendez-vous de mardi",
]

for i, msg in enumerate(conversation_2, 1):
    print(f"\n👤 Client (msg {i}): {msg}")
    try:
        response = run_agent(msg, test_phone_2)
        print(f"🤖 Agent: {response}")
    except Exception as e:
        print(f"❌ Erreur: {e}")

# Test 3: Question sur les horaires
print("\n\n" + "=" * 70)
print("TEST 3: Question sur les jours d'ouverture")
print("=" * 70)
test_phone_3 = "0612345673"
conversation_3 = [
    "vous êtes ouverts le dimanche ?",
    "et le lundi alors ?",
    "d'accord, merci",
]

for i, msg in enumerate(conversation_3, 1):
    print(f"\n👤 Client (msg {i}): {msg}")
    try:
        response = run_agent(msg, test_phone_3)
        print(f"🤖 Agent: {response}")
    except Exception as e:
        print(f"❌ Erreur: {e}")

print("\n\n" + "="*70)
print("✅ TESTS TERMINÉS")
print("="*70 + "\n")
