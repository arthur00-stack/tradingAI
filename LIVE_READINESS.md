# Passage au réel — décisions et critères de mise en service

Statut : NON PRÊT POUR LE LIVE. L'application reste paper. Aucun ordre réel,
transfert ou dépôt bancaire n'a été effectué. Aucun compte externe n'est connecté.

## Décisions indispensables

Choix reçus : IBKR, Forex, résidence France, aucun compte existant, capital 10 EUR,
perte maximale totale 1 EUR, levier souhaité 30 ou 50, shorts interdits. Le plafond
30 des CFD majeurs retail est retenu comme souhait non activé. Voir SECURITY.md :
les coûts ne permettent pas de valider ce projet au budget demandé. Restent à fixer
le produit exact accessible, la paire, l'horizon et la fréquence de trading. Les limites
10 % d'exposition et 5 % de drawdown du cahier initial restent des contraintes
logicielles, pas une recommandation d'allocation. Aucun montant réel ne sera inféré.

## Ce qui a été renforcé

- Le drawdown utilise désormais un indice de performance chaîné, neutralisant
  les dépôts/retraits. Le moteur observe la valeur avant transfert. Retirer du
  capital après une perte ne multiplie plus artificiellement le pourcentage de
  drawdown ; déposer du capital ne masque plus cette perte.
- Une ancienne base de risque dépourvue de cet indice entraîne une suspension
  explicite. Son historique n'est pas assez précis pour reconstituer les flux.
- `broker/ledger.py` ajoute un registre SQLite indépendant : intentions durables,
  réservations atomiques, états SUBMITTING/UNKNOWN/PARTIAL/CANCEL_PENDING,
  déduplication des fills et prise en compte des fills arrivant après annulation.

Le registre est **un composant testé, pas encore connecté au moteur paper ni à
un broker réel**. Son intégration doit être faite avec les identifiants, comptes,
cursors et garanties du broker choisi. Ses réservations restent conservatrices
à leur montant initial pendant l'ordre. Il faut rapprocher cash, fills et ordres
en une vue cohérente avant de réutiliser une réservation terminale. Un statut
CANCELED ne prouve pas à lui seul l'absence de fills tardifs. Les corrections
et annulations de transactions nécessitent des événements comptables distincts.

## Exigences non couvertes par le premier cahier des charges

| Domaine | Risques/paramètres à intégrer | Preuve attendue avant mise en service |
|---|---|---|
| Stratégie | Horizon, univers, concurrence, capacité du capital, turnover, perte du signal | Gain net hors échantillon face aux baselines, avec incertitude statistique |
| Données | Biais de survivance, look-ahead, dividendes/splits, trous, désynchronisation | Données point-in-time versionnées, contrôles de qualité et tests de causalité |
| Validation | Surapprentissage, sélection parmi beaucoup d'essais, fuite entre périodes | Walk-forward, purge/embargo adaptés à l'horizon, test final intact, journal des essais |
| Coûts | Commissions minimales, spread, impact, emprunt, funding, change, fiscalité applicable | Simulation calibrée aux fills observés et stress de coûts/liquidité |
| Exécution | Fills partiels, timeout ambigu, cancel/replace, rate limits, quantité minimale | Tests broker sandbox et rapprochement des exécutions sans duplication |
| Risque | Perte absolue, drawdown global, concentration/corrélation, gaps, liquidité | Limites explicites indépendantes de l'IA, tests de stress et suspension persistante |
| Capital | Cash réservé, réglé, retirable, frais/fiscalité provisionnés | Ledger rapproché au compte ; pas de conversion automatique de budget en dépôt |
| Retraits | Délais de règlement, ventes imposables, bénéficiaire, statut inconnu | Adaptateur trésorerie dédié et rapprochement de chaque demande |
| Sécurité | Secrets, droits excessifs, dépendances compromises, accès GUI | Secrets hors dépôt, permissions minimales, bénéficiaires autorisés, audit d'accès |
| Exploitation | Coupure réseau, panne machine, disque plein, crash, déconnexion broker | Watchdog externe, alertes testées, sauvegarde restaurée et exercice de reprise |
| Modèles | Drift, mismatch features, modèle corrompu, mise à jour en cours de séance | Artefact versionné, schéma figé, rollback, seuils de dégradation suivis |
| Juridique | Pays, marché, gestion de fonds tiers, données sous licence, obligations fiscales | Vérification selon juridiction et compte réels ; aucun régime n'est présumé |

Une interface locale ne protège pas le compte si la machine s'éteint. Les limites
hébergées au broker et le watchdog externe doivent fonctionner indépendamment de
Qt et du modèle. Un ordre au marché garantit encore moins un prix en crise ;
la politique d'urgence doit traiter marchés fermés, spreads extrêmes et actifs
suspendus. On ne peut garantir la liquidation ni plafonner exactement la perte
lors d'un gap.

## Définir « compétitif » avec des résultats mesurables

La complexité LSTM/A2C n'est pas un avantage démontré. Comparer au minimum à HOLD,
au cash et à une règle simple correspondant au marché/horizon. Évaluer rendement
net, drawdown, expected shortfall, turnover, stabilité par régime, exposition,
capacité et coûts d'exploitation. Le Sharpe n'a de sens comparable qu'avec une
fréquence/calendrier cohérents ; la GUI actuelle affiche seulement un ratio sur
échantillons, non un Sharpe annualisé validé.

Aucun seuil de rentabilité ni nombre minimal universel de trades n'est inventé.
La taille du test et la durée paper dépendent de la fréquence et de l'autocorrélation.
Conserver un test final intact : rejouer et retuner sans cesse sur cette période
la transforme en données d'entraînement. Les essais infructueux comptent dans
l'évaluation du biais de sélection.

## Séquence de réalisation après choix du broker

1. Connecteur en lecture : identité du compte, balances, positions, ordres ouverts,
   fills et capacité de retrait. Stockage des secrets localement, jamais dans le chat.
2. Connecteur d'exécution sandbox avec registre durable ; couverture des rejets,
   timeout après acceptation, fills partiels, annulation concurrente et redémarrage.
3. Données et protocole quantitatif fixés, puis entraînement et test hors échantillon.
4. Exécution paper avec vrais flux, mesure des écarts avec le simulateur, tests de
   panne/alerte/reprise. Le flux synthétique actuel ne constitue pas cette validation.
5. Mise en service limitée au montant et aux limites explicitement définis,
   après résolution des critères ci-dessus. Augmentation éventuelle fondée sur les
   résultats d'exécution et de risque ; pas sur une promesse de rendement.

## Références officielles

- [CCXT : précision, limites, sandbox et erreurs d'API](https://docs.ccxt.com/docs/manual)
- [IBKR : API et opérations de trading](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/)
- [AMF : limites de l'IA pour investir](https://www.amf-france.org/fr/espace-epargnants/actualites-mises-en-garde/utiliser-lintelligence-artificielle-pour-investir-quoi-faut-il-faire-attention)
