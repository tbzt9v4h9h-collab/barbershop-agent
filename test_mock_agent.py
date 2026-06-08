#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script de test du nouvel agent barbershop avec des mocks (pas de vraie API OpenAI)
"""

import sys
import os

# Mock les appels OpenAI
class MockResponse:
    def __init__(self, content, tool_calls=None):
        self.choices = [MockChoice(content, tool_calls)]

class MockChoice:
    def __init__(self, content, tool_calls=None):
        self.message = MockMessage(content, tool_calls)

class MockMessage:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

# Patcher openai avant l'import
import unittest.mock as mock

with mock.patch('openai.OpenAI') as mock_openai:
    # Créer une instance mock
    mock_client = mock.MagicMock()
    mock_openai.return_value = mock_client

    # Importer le module
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent", "agent ia finish.py")
    agent_module = importlib.util.module_from_spec(spec)
    sys.modules["agent"] = agent_module
    spec.loader.exec_module(agent_module)

    # Récupérer la fonction
    run_agent = agent_module.run_agent

    # Configurer les mocks pour les 3 tests
    test_responses = {
        "bonjour": "Bonjour! Bienvenue au salon Chez les fdp du dégradé. Comment puis-je vous aider?",
        "pour demain": "Vous souhaitez un rendez-vous pour demain, c'est ça? À quelle heure vous convient?",
        "14h": "Parfait! Vous avez dit homme coupe vers 14h. Je vais vérifier la disponibilité.",
        "salut": "Bonjour Thomas! Comment puis-je vous aider?",
        "annuler": "Vous voulez annuler votre rendez-vous de mardi? Je peux vous aider.",
        "dimanche": "Non, malheureusement nous sommes fermés le dimanche. Nous sommes ouverts du mardi au samedi.",
        "lundi": "Et lundi aussi nous sommes fermés. Nos horaires sont : mardi à samedi, de 09:00 à 18:00.",
    }

    print("\n" + "="*70)
    print("🎤 TEST DES 3 CONVERSATIONS (AVEC MOCKS)")
    print("="*70 + "\n")

    # Test 1
    print("=" * 70)
    print("TEST 1: Demande de RDV complexe")
    print("=" * 70)

    test_phone_1 = "0612345671"
    conv_1 = [
        "bonjour j'aimerais prendre rdv pour demain pour un homme, une coupe et si possible vers 14h",
    ]

    for i, msg in enumerate(conv_1, 1):
        print(f"\n👤 Client (msg {i}): {msg}")

        # Mock la réponse
        response_text = "Parfait! Vous souhaitez un RDV pour un homme, coupe, demain vers 14h? Pouvez-vous me donner votre prénom?"
        mock_client.chat.completions.create.return_value = MockResponse(response_text)

        try:
            response = run_agent(msg, test_phone_1)
            print(f"🤖 Agent: {response}")
        except Exception as e:
            print(f"❌ Erreur: {e}")

    # Test 2
    print("\n\n" + "=" * 70)
    print("TEST 2: Annulation de rendez-vous")
    print("=" * 70)

    test_phone_2 = "0612345672"
    conv_2 = [
        "salut, c'est Thomas",
        "je voudrais annuler mon rendez-vous de mardi",
    ]

    for i, msg in enumerate(conv_2, 1):
        print(f"\n👤 Client (msg {i}): {msg}")

        # Mock les réponses
        if i == 1:
            response_text = "Bonjour Thomas! Comment puis-je vous aider?"
        else:
            response_text = "Je vais annuler votre rendez-vous de mardi. C'est confirmé, votre RDV est annulé."

        mock_client.chat.completions.create.return_value = MockResponse(response_text)

        try:
            response = run_agent(msg, test_phone_2)
            print(f"🤖 Agent: {response}")
        except Exception as e:
            print(f"❌ Erreur: {e}")

    # Test 3
    print("\n\n" + "=" * 70)
    print("TEST 3: Question sur les horaires")
    print("=" * 70)

    test_phone_3 = "0612345673"
    conv_3 = [
        "vous êtes ouverts le dimanche ?",
        "et le lundi alors ?",
        "d'accord, merci",
    ]

    for i, msg in enumerate(conv_3, 1):
        print(f"\n👤 Client (msg {i}): {msg}")

        # Mock les réponses
        if i == 1:
            response_text = "Non, malheureusement nous sommes fermés le dimanche. Nous sommes ouverts du mardi au samedi."
        elif i == 2:
            response_text = "Et lundi aussi nous sommes fermés. Nos horaires sont : mardi à samedi, de 09:00 à 18:00."
        else:
            response_text = "De rien! N'hésitez pas à nous rappeler pour prendre rendez-vous. Au revoir!"

        mock_client.chat.completions.create.return_value = MockResponse(response_text)

        try:
            response = run_agent(msg, test_phone_3)
            print(f"🤖 Agent: {response}")
        except Exception as e:
            print(f"❌ Erreur: {e}")

    print("\n\n" + "="*70)
    print("✅ TESTS TERMINÉS")
    print("="*70 + "\n")
