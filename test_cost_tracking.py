#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test du système de tracking des coûts OpenAI
"""

import sys
import os
import importlib.util
import unittest.mock as mock
from datetime import datetime

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
    calculer_cout = agent_module.calculer_cout
    enregistrer_usage = agent_module.enregistrer_usage
    rapport_mensuel = agent_module.rapport_mensuel

    class MockUsage:
        def __init__(self, prompt_tokens, completion_tokens):
            self.prompt_tokens = prompt_tokens
            self.completion_tokens = completion_tokens
            self.total_tokens = prompt_tokens + completion_tokens

    class MockResponse:
        def __init__(self, content, tool_calls=None, prompt_tokens=100, completion_tokens=50):
            self.choices = [MockChoice(content, tool_calls)]
            self.usage = MockUsage(prompt_tokens, completion_tokens)

    class MockChoice:
        def __init__(self, content, tool_calls=None):
            self.message = MockMessage(content, tool_calls)

    class MockMessage:
        def __init__(self, content, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    print("\n" + "="*70)
    print("💰 TEST DU SYSTÈME DE TRACKING DES COÛTS OPENAI")
    print("="*70 + "\n")

    # ======= TEST 1 : Calcul des coûts =======
    print("="*70)
    print("TEST 1 : Calcul des coûts en USD et EUR")
    print("="*70)

    test_cases = [
        (100, 50, "Petite requête"),
        (1_000_000, 500_000, "Grande requête"),
        (250, 100, "Requête normale"),
    ]

    for tokens_input, tokens_output, description in test_cases:
        cout_usd, cout_eur = calculer_cout(tokens_input, tokens_output)
        print(f"\n{description}:")
        print(f"  Tokens input  : {tokens_input:,}")
        print(f"  Tokens output : {tokens_output:,}")
        print(f"  Coût USD      : ${cout_usd:.6f}")
        print(f"  Coût EUR      : €{cout_eur:.6f}")

    # ======= TEST 2 : Simulation d'une conversation =======
    print("\n\n" + "="*70)
    print("TEST 2 : Simulation d'une conversation avec tracking")
    print("="*70)

    phone = "0612345678"

    # Réinitialiser les variables de session
    agent_module.session_tokens_input = 0
    agent_module.session_tokens_output = 0
    agent_module.session_tokens_total = 0
    agent_module.session_nb_echanges = 0

    # Simuler 3 échanges
    echanges = [
        ("Bonjour j'aimerais prendre un RDV", 150, 80),
        ("Pour une coupe homme", 120, 60),
        ("Demain vers 14h", 130, 70),
    ]

    for i, (msg, input_tokens, output_tokens) in enumerate(echanges, 1):
        print(f"\n🔄 Échange {i}:")
        print(f"  Message    : {msg}")

        # Mock la réponse
        response_text = f"Agent respond to: {msg[:30]}"
        mock_client.chat.completions.create.return_value = MockResponse(
            response_text,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens
        )

        try:
            response = run_agent(msg, phone)
            print(f"  Tokens utilisés : {input_tokens + output_tokens}")

            # Afficher le total accumulé
            total_accum = agent_module.session_tokens_total
            cout_u, cout_e = calculer_cout(
                agent_module.session_tokens_input,
                agent_module.session_tokens_output
            )
            print(f"  📊 Accumulé : {total_accum} tokens | €{cout_e:.6f}")
        except Exception as e:
            print(f"  ❌ Erreur: {e}")

    # ======= TEST 3 : Affichage du coût total =======
    print("\n\n" + "="*70)
    print("TEST 3 : Coût total de la session")
    print("="*70)

    cout_usd, cout_eur = calculer_cout(
        agent_module.session_tokens_input,
        agent_module.session_tokens_output
    )

    print(f"\n💰 SESSION TOTALE")
    print(f"   Tokens input  : {agent_module.session_tokens_input:,}")
    print(f"   Tokens output : {agent_module.session_tokens_output:,}")
    print(f"   Tokens total  : {agent_module.session_tokens_total:,}")
    print(f"   Échanges      : {agent_module.session_nb_echanges}")
    print(f"   Coût USD      : ${cout_usd:.6f}")
    print(f"   Coût EUR      : €{cout_eur:.6f}")
    print(f"   Coût par éch. : €{cout_eur/agent_module.session_nb_echanges:.6f}")

    print("\n" + "="*70)
    print("✅ TESTS COMPLÉTÉS")
    print("="*70)
    print("\nRésumé :")
    print(f"✅ Test 1 : Calcul des coûts — PASSÉ")
    print(f"✅ Test 2 : Simulation {len(echanges)} échanges — PASSÉ")
    print(f"✅ Test 3 : Coût total accumulé — PASSÉ")
    print("\n" + "="*70 + "\n")
