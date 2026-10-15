# Recherche — évaluation et entraînement

- Évaluation chronologique séparée du compte, comparaisons cash/achat conservé,
  coûts stressés et mesure de latence CPU ; rapports JSON et HTML.
- Entraînement A2C à mémoire de gradients bornée et traçabilité des checkpoints.
- Aucun remplacement du modèle de démonstration, aucune qualification pour le réel.
- Validation : 79 tests, typage strict sur 25 fichiers source, ruff conforme.

# Version 0.2.1 — réactivation et retraits

- Réactivation manuelle après vérification du compte et du flux ; nouveau seuil
  de session de 5 %, historique et limite de perte cumulée conservés.
- PANIC reste opérationnel après chaque réactivation ; un nouveau PANIC a priorité
  sur une demande de réactivation déjà en attente.
- Retraits simulés accessibles après suspension ou liquidation terminée.
- Une panne d'inférence laisse les commandes de retrait accessibles.
- Messages d'opération persistants dans le tableau de bord.

# Version 0.2.0 — livraison paper

Périmètre livré : simulation locale autonome et observable, pas trading réel.

- Lancement unique, modèle de démonstration préparé si absent, financement initial
  de 10 EUR fictifs sans réapprovisionnement automatique.
- Arrêt à 5 % de perte ou drawdown journalier, liquidation, suspension persistante
  et reprise d'une liquidation interrompue.
- Persistance du dernier prix, réconciliation des retraits terminés, erreurs de
  liquidation visibles, interdiction de transférer après déclenchement du risque.
- Historique graphique persistant, table des exécutions et frais, exports HTML,
  JSON et CSV manuels ou automatiques à la fermeture et après liquidation.
- Rapports sans scripts externes, échappement HTML/CSV, fichiers privés,
  remplacement atomique des rapports et checkpoints.
- Distribution wheel et archive source sans bases, secrets ni données personnelles.

Validation : 61 tests réussis sous Python 3.14.4 ; mypy strict conforme sur 23 fichiers
source ; ruff analyse/formatage conformes ; scripts shell vérifiés ; wheel construit.
Deux avertissements Gymnasium attendus concernent les bornes infinies d'observation.
Interface testée et inspectée en mode offscreen. Pas de test d'exécution IBKR réelle.

Le modèle synthétique n'a pas été validé sur des données historiques Forex réelles.
L'application ne promet aucun rendement et ne peut garantir le prix de liquidation.
Le passage au réel reste hors livraison : voir LIVE_READINESS.md et SECURITY.md.
