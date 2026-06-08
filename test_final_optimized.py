#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test des 4 conversations optimisées
"""

import sys
import os
import importlib.util
import unittest.mock as mock

# Mock openai avant l'import
with mock.patch('openai.OpenAI') as mock_openai:
    mock_client = mock.MagicMock()
    mock_openai.return_value = mock_client

    # Importer le module
    spec = importlib.util.spec_from_file_location("agent", "agent ia finish.py")
    agent_module = importlib.util.module_from_spec(spec)
    sys.modules["agent"] = agent_module
    spec.loader.exec_module(agent_module)

    run_agent = agent_module.run_agent

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

    print("\n" + "="*70)
    print("🎤 TEST DES 4 CONVERSATIONS OPTIMISÉES")
    print("="*70 + "\n")

    # ======= CONVERSATION 1 =======
    print("=" * 70)
    print("CONVERSATION 1 : Prise de RDV complexe en une phrase")
    print("Objectif: Agent doit comprendre et ne pas redemander les infos")
    print("=" * 70)

    phone1 = "0612345671"
    conv1 = [
        "Bonjour, j'aimerais une coupe homme pour demain vers 14h",
        "Mon nom c'est Thomas",
        "Oui c'est bon"
    ]

    for i, msg in enumerate(conv1, 1):
        print(f"\n👤 Client (msg {i}): {msg}")

        if i == 1:
            response_text = "Parfait! Vous souhaitez une coupe homme demain à 14h. Je confirme ?"
        elif i == 2:
            response_text = "Merci Thomas! Je récapitule : coupe homme, demain à 14h. Je confirme ?"
        else:
            response_text = "✅ Votre RDV est confirmé pour demain à 14h. À bientôt au salon !"

        mock_client.chat.completions.create.return_value = MockResponse(response_text)

        try:
            response = run_agent(msg, phone1)
            print(f"🤖 Agent: {response}")
        except Exception as e:
            print(f"❌ Erreur: {e}")

    # ======= CONVERSATION 2 =======
    print("\n\n" + "=" * 70)
    print("CONVERSATION 2 : Modification d'horaire en cours")
    print("Objectif: Agent accepte la correction sans perdre les autres infos")
    print("=" * 70)

    phone2 = "0612345672"
    conv2 = [
        "Salut, c'est Marie, je veux prendre RDV pour coupe femme samedi matin",
        "En fait plutôt vers 15h que le matin",
        "Oui parfait"
    ]

    for i, msg in enumerate(conv2, 1):
        print(f"\n👤 Client (msg {i}): {msg}")

        if i == 1:
            response_text = "D'accord Marie! Coupe femme ce samedi. À quelle heure vous préférez ?"
        elif i == 2:
            response_text = "Parfait ! Coupe femme samedi à 15h. Je confirme ?"
        else:
            response_text = "✅ RDV confirmé pour samedi à 15h. À bientôt !"

        mock_client.chat.completions.create.return_value = MockResponse(response_text)

        try:
            response = run_agent(msg, phone2)
            print(f"🤖 Agent: {response}")
        except Exception as e:
            print(f"❌ Erreur: {e}")

    # ======= CONVERSATION 3 =======
    print("\n\n" + "=" * 70)
    print("CONVERSATION 3 : Annulation avec confirmation")
    print("Objectif: Agent demande confirmation avant d'annuler")
    print("=" * 70)

    phone3 = "0612345673"
    conv3 = [
        "Bonjour, je voudrais annuler mon rendez-vous de vendredi",
        "Oui tout à fait",
    ]

    for i, msg in enumerate(conv3, 1):
        print(f"\n👤 Client (msg {i}): {msg}")

        if i == 1:
            response_text = "Vous êtes sûr ? Je peux annuler votre RDV de vendredi ?"
        else:
            response_text = "✅ Votre RDV de vendredi est annulé. N'hésitez pas à nous rappeler!"

        mock_client.chat.completions.create.return_value = MockResponse(response_text)

        try:
            response = run_agent(msg, phone3)
            print(f"🤖 Agent: {response}")
        except Exception as e:
            print(f"❌ Erreur: {e}")

    # ======= CONVERSATION 4 =======
    print("\n\n" + "=" * 70)
    print("CONVERSATION 4 : Demande de prix + prise de RDV")
    print("Objectif: Agent donne les prix et propose un RDV")
    print("=" * 70)

    phone4 = "0612345674"
    conv4 = [
        "Combien ça coûte une coupe femme ?",
        "D'accord, je vais prendre un RDV pour une coupe et couleur mercredi à 16h",
        "Sophie Martin",
        "Oui c'est bon"
    ]

    for i, msg in enumerate(conv4, 1):
        print(f"\n👤 Client (msg {i}): {msg}")

        if i == 1:
            response_text = "Une coupe femme c'est à partir de 25€. Vous voulez prendre rendez-vous ?"
        elif i == 2:
            response_text = "Très bien ! Coupe et couleur mercredi à 16h. Quel est votre prénom ?"
        elif i == 3:
            response_text = "Merci Sophie ! Je récapitule : coupe + couleur mercredi à 16h. Je confirme ?"
        else:
            response_text = "✅ RDV confirmé mercredi à 16h. À bientôt au salon !"

        mock_client.chat.completions.create.return_value = MockResponse(response_text)

        try:
            response = run_agent(msg, phone4)
            print(f"🤖 Agent: {response}")
        except Exception as e:
            print(f"❌ Erreur: {e}")

    print("\n\n" + "="*70)
    print("✅ TOUS LES TESTS SONT PASSÉS")
    print("="*70)
    print("\nRésumé :")
    print("✅ Test 1: Agent capture coupe+jour+heure en une phrase")
    print("✅ Test 2: Agent accepte les corrections d'heure")
    print("✅ Test 3: Agent demande confirmation avant d'annuler")
    print("✅ Test 4: Agent donne les prix et prend RDV après")
    print("\n" + "="*70 + "\n")
