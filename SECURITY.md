# Sécurité et faisabilité — IBKR, France, capital 10 EUR

## Conclusion opérationnelle

Ne pas financer un compte pour exécuter cette stratégie en l'état. Le profil demandé
est enregistré dans `config/ibkr-fr.json`, mais l'application reste une simulation
locale. Aucun compte IBKR n'existe, aucun identifiant n'a été demandé et aucun
connecteur IBKR n'est installé ou connecté.

Les tarifs directs IBKR consultés le 30 septembre 2026 indiquent un minimum de
2 USD par ordre au premier palier pour le change spot et les CFD Forex : un achat
puis une vente représentent 4 USD avant spread, slippage et financement. Cela
n'est pas adapté à un budget total de perte de 1 EUR. La conversion automatique de
devise liée à d'autres opérations est un service distinct, pas un contournement
pour une stratégie Forex à levier. L'accès aux produits et les conditions effectives
dépendront du compte, de l'entité IBKR et des autorisations.

En France, pour un particulier, le plafond CFD sur les principales paires de devises
est 30:1 ; 50:1 n'est pas retenu pour ce projet. Ce plafond ne garantit ni l'accès
au produit ni une marge suffisante chez IBKR. Le seuil IdealPro de 20 000 EUR
publié pour les ordres standard ne doit pas être confondu avec un minimum universel
de conversion : des petits ordres peuvent suivre un autre circuit.

À exposition théorique de 300 EUR avec 10 EUR de fonds et un levier 30, un mouvement
défavorable d'environ 0,333 % coûte 1 EUR avant frais (approximation dans la devise
de compte). Le bot ne peut garantir ce plafond : stop marché, stop limite, panne,
spread et gap ont des risques différents. La protection réglementaire contre le
solde négatif d'un CFD éligible ne garantit pas de conserver 9 EUR sur 10.

## Contrôles implémentés et raccordés à l'application

- Profil par défaut : capital autorisé 10 EUR, perte nette cumulée 1 EUR, aucune
  position nette négative, aucun ordre réel, aucun virement réel.
- Levier 30 enregistré comme **souhait**, jamais comme permission d'exécution.
  Le moteur actuel reste spot sans levier. Le cap historique d'exposition de 10 %
  reste actif : il s'agit de notionnel, pas de marge. Il n'est pas converti en 300 EUR.
- La limite journalière initiale de 5 % reste également active : environ 0,50 EUR
  sur une référence de 10 EUR. Elle suspend les décisions IA et demande la liquidation.
  Une perte de 5 % depuis le début de la session déclenche aussi cet arrêt, sans remise à
  zéro à minuit. Seule la réactivation manuelle vérifiée ouvre une nouvelle session,
  journalisée ; le PnL historique et la limite globale de 1 EUR restent conservés. Demande de liquidation et arrêt sont conservés après redémarrage.
  La perte nette
  cumulée de 1 EUR déclenche l'Event panic et une demande de liquidation par le
  thread indépendant. Les frais comptabilisés sont inclus dans l'equity.
- La perte nette cumulée est `dépôts_nets - equity` : un dépôt ne renouvelle pas
  automatiquement le budget de perte. Les gains peuvent compenser les pertes ;
  il ne s'agit pas d'une somme des seuls trades perdants ni d'un trailing stop.
- Aucun accroissement automatique du capital. Les dépôts paper dépassant le
  capital net autorisé sont refusés. Modifier le capital demande une décision
  explicite et un profil revu ; les performances ne changent pas les permissions.
- Schéma strict : refus de `live_enabled=true`, des virements activés, de l'API
  hors loopback, de l'écriture API et des shorts dans ce profil de préparation.
  Ce schéma ne change aucun paramètre dans TWS : il n'y a pas encore de connexion.
- SQLite : fichiers 0600, nouveaux répertoires d'état 0700, refus des liens
  symboliques et fichiers spéciaux, refus des répertoires modifiables par d'autres
  utilisateurs. L'application initialise un umask 077.
- Les secrets, fichiers .env, clés privées et bases SQLite sont exclus de Git.
  Gitignore n'est pas un coffre-fort. Le système n'enregistre aucun mot de passe IBKR.
- L'inférence est séparée et ne reçoit ni API broker ni secret. Cela ne constitue
  pas un sandbox OS : du code malveillant sous le même utilisateur reste dangereux.

La vente pour clôturer une position longue est autorisée ; ouvrir une position
négative sur la paire ne l'est pas. Une position Forex a toujours deux jambes
(long une devise, short l'autre économiquement) : cette convention long-only porte
sur la paire choisie, pas sur l'absence universelle d'exposition baissière.

## Configuration du futur compte — à faire lors de sa création

1. Créer soi-même le compte sur le domaine officiel IBKR, renseigner honnêtement
   les informations d'identité et d'expérience ; vérifier l'entité contractante.
   Ne pas demander un statut professionnel pour contourner les plafonds retail.
2. Activer IB Key / l'authentification à deux facteurs. Ne jamais transmettre
   mot de passe, code temporaire ou validation de connexion dans un chat.
3. Installer TWS/IB Gateway officiel. Pour l'intégration initiale, conserver
   `Read-Only API` et `Allow connections from localhost only`. N'exposer aucun
   port TWS sur Internet. Les réglages broker devront être vérifiés dans TWS.
4. Compte paper et compte live identifiés explicitement. Vérifier l'identifiant
   autorisé lors de chaque connexion ; le numéro de port ne prouve pas à lui seul
   qu'il s'agit du paper. Ne pas utiliser un client qui prend le contrôle de tous
   les ordres manuels et comptes liés sans périmètre défini.
5. Les retraits réels restent hors du processus de trading : aucune autorité de
   virement donnée au modèle. Prévoir un chemin de trésorerie séparé et des
   bénéficiaires contrôlés, si cette fonctionnalité devient réalisable.

## Limites et travaux sécurité restants

Les permissions locales ne protègent pas contre root, une compromission du compte
OS, un modèle ou une dépendance malveillante. Il n'y a encore ni chiffrement du
disque, ni sauvegarde chiffrée automatisée/restaurée, ni watchdog externe, ni
pare-feu configuré, ni audit indépendant. Ces opérations ne sont pas prétendues
réalisées. L'intégration future nécessite aussi revue des dépendances, déploiement
sous utilisateur dédié, séparation de privilèges, vérification des artefacts modèle,
rotation/révocation des accès, alerte de déconnexion et test de reprise.

Le mécanisme de liquidation est un contrôle de réaction : son test dans le mock
ne prouve ni un délai ni un prix d'exécution chez IBKR. Avec l'exigence d'une perte
**strictement garantie** de 1 EUR, ce trading à levier ne peut pas être accepté.

## Sources officielles

- [IBKR spot : commissions](https://www.interactivebrokers.com/en/pricing/commissions-spot-currencies.php)
- [IBKR Ireland : commissions CFD Forex](https://www.interactivebrokers.ie/en/pricing/commissions-cfd-forex.php)
- [IBKR : tailles standard des ordres de change](https://investors.interactivebrokers.com/en/?f=5479)
- [AMF : CFD et plafond de levier](https://www.amf-france.org/fr/espace-epargnants/comprendre-les-produits-financiers/produits-complexes/cfd)
- [IBKR : types d'ordres et limites d'exécution](https://www.interactivebrokers.com/en/trading/ordertypes.php)
- [IBKR : IB Key](https://www.ibkrguides.com/securelogin/sls/activating-two-factor-via-mobile.htm)
- [IBKR : mode API lecture seule](https://www.interactivebrokers.com/en/?f=%2Fen%2Fgeneral%2Ftws-notes-950.php)
