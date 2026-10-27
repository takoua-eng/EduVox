"""Contenu des données de démonstration (commande peupler_demo) : enseignants, cours, quiz, échanges avec l'assistant."""

# (prénom, nom, matière enseignée)
ENSEIGNANTS = [
    ("Sami", "Trabelsi", "Mathématiques"),
    ("Leïla", "Ben Ali", "Informatique"),
    ("Karim", "Haddad", "Physique"),
    ("Nour", "Jaziri", "Anglais"),
]

PRENOMS = ["Amira", "Youssef", "Ines", "Mehdi", "Sarra", "Aziz", "Rania", "Omar", "Yasmine", "Hamza",
           "Mariem", "Fares", "Salma", "Anis", "Eya", "Walid", "Nesrine", "Bilel", "Hiba", "Skander"]
NOMS = ["Ben Salah", "Gharbi", "Mansouri", "Chaabane", "Ayari", "Dridi", "Hammami", "Kefi", "Sassi", "Zouari",
        "Mejri", "Belhadj", "Jlassi", "Toumi", "Ferchichi", "Khelifi", "Bouazizi", "Rekik", "Masmoudi", "Tlili"]

# Chaque cours : texte (comme une transcription), résumé, et questions (énoncé, choix, indice de la bonne réponse)
COURS = {
    "Mathématiques": [
        {
            "titre": "Matrices et opérations",
            "niveau": "debutant",
            "mots_cles": "matrices, algèbre linéaire, produit matriciel",
            "description": "Définitions, addition et produit de matrices, matrice identité.",
            "texte": "Une matrice est un tableau de nombres organisé en lignes et en colonnes. On additionne deux matrices "
                     "de même taille terme à terme. Le produit AB existe si le nombre de colonnes de A est égal au nombre "
                     "de lignes de B. Le produit matriciel n'est pas commutatif : en général AB est différent de BA. "
                     "La matrice identité joue le rôle du nombre 1 : AI = IA = A.",
            "resume": "- Une matrice est un tableau de nombres (lignes × colonnes).\n- Addition terme à terme, matrices de même taille.\n"
                      "- Produit AB possible si colonnes(A) = lignes(B) ; il n'est pas commutatif.\n- La matrice identité I vérifie AI = IA = A.",
            "questions": [
                ("Quand le produit AB est-il défini ?", ["Si A et B sont carrées", "Si colonnes(A) = lignes(B)",
                                                         "Si lignes(A) = lignes(B)", "Toujours"], 1),
                ("Le produit matriciel est commutatif.", ["Vrai", "Faux"], 1),
                ("Que vaut AI, où I est la matrice identité ?", ["I", "0", "A", "A²"], 2),
            ],
        },
        {
            "titre": "Déterminants et inverse",
            "niveau": "intermediaire",
            "mots_cles": "déterminant, matrice inverse, algèbre linéaire",
            "description": "Calcul du déterminant 2×2 et 3×3, condition d'inversibilité.",
            "texte": "Le déterminant d'une matrice 2×2 [[a, b], [c, d]] vaut ad − bc. Une matrice carrée est inversible "
                     "si et seulement si son déterminant est non nul. Pour une matrice 3×3, on peut développer selon une "
                     "ligne ou une colonne. Le déterminant d'un produit est le produit des déterminants.",
            "resume": "- det([[a, b], [c, d]]) = ad − bc.\n- Inversible ⇔ déterminant non nul.\n"
                      "- Développement selon une ligne pour les 3×3.\n- det(AB) = det(A) × det(B).",
            "questions": [
                ("Que vaut le déterminant de [[2, 1], [4, 3]] ?", ["2", "10", "−2", "5"], 0),
                ("Une matrice de déterminant nul est inversible.", ["Vrai", "Faux"], 1),
                ("det(AB) est égal à :", ["det(A) + det(B)", "det(A) × det(B)", "det(A) − det(B)", "1"], 1),
            ],
        },
        {
            "titre": "Probabilités : notions de base",
            "niveau": "debutant",
            "mots_cles": "probabilités, événements, dénombrement",
            "description": "Univers, événements, probabilité uniforme et événements indépendants.",
            "texte": "L'univers est l'ensemble des issues possibles d'une expérience aléatoire. Un événement est une "
                     "partie de l'univers. En situation d'équiprobabilité, P(A) = nombre de cas favorables / nombre de cas "
                     "possibles. Deux événements A et B sont indépendants si P(A ∩ B) = P(A) × P(B).",
            "resume": "- Univers = ensemble des issues ; événement = partie de l'univers.\n"
                      "- Équiprobabilité : P(A) = cas favorables / cas possibles.\n- Indépendance : P(A ∩ B) = P(A) × P(B).",
            "questions": [
                ("On lance un dé équilibré. Probabilité d'obtenir un nombre pair ?", ["1/6", "1/3", "1/2", "2/3"], 2),
                ("Une probabilité peut être supérieure à 1.", ["Vrai", "Faux"], 1),
                ("A et B sont indépendants si :", ["P(A ∩ B) = P(A) × P(B)", "P(A ∪ B) = 1",
                                                   "P(A) = P(B)", "A ∩ B est vide"], 0),
            ],
        },
    ],
    "Informatique": [
        {
            "titre": "Introduction au langage Python",
            "niveau": "debutant",
            "mots_cles": "python, variables, boucles, programmation",
            "description": "Variables, types, conditions et boucles en Python.",
            "texte": "Python est un langage interprété. Une variable est créée lors de sa première affectation. "
                     "Les types de base sont int, float, str et bool. La boucle for parcourt une séquence, la boucle while "
                     "répète tant qu'une condition est vraie. L'indentation délimite les blocs de code.",
            "resume": "- Langage interprété ; variables créées à l'affectation.\n- Types : int, float, str, bool.\n"
                      "- for parcourt une séquence, while répète tant qu'une condition est vraie.\n- L'indentation délimite les blocs.",
            "questions": [
                ("Quel mot-clé crée une boucle qui parcourt une séquence ?", ["while", "for", "loop", "repeat"], 1),
                ("En Python, l'indentation délimite les blocs de code.", ["Vrai", "Faux"], 0),
                ("Quel est le type de la valeur 3.5 ?", ["int", "str", "float", "bool"], 2),
            ],
        },
        {
            "titre": "Bases de données relationnelles et SQL",
            "niveau": "intermediaire",
            "mots_cles": "sql, bases de données, requêtes, jointures",
            "description": "Tables, clés primaires et étrangères, requêtes SELECT et jointures.",
            "texte": "Une base de données relationnelle organise les données en tables. Chaque ligne est identifiée par "
                     "une clé primaire. Une clé étrangère référence la clé primaire d'une autre table. La requête SELECT "
                     "lit des données ; WHERE filtre les lignes ; JOIN combine plusieurs tables sur une condition.",
            "resume": "- Données en tables ; clé primaire = identifiant unique.\n- Clé étrangère = lien vers une autre table.\n"
                      "- SELECT lit, WHERE filtre, JOIN combine des tables.",
            "questions": [
                ("Quelle clause filtre les lignes d'une requête ?", ["ORDER BY", "WHERE", "GROUP BY", "FROM"], 1),
                ("Une clé étrangère référence une clé primaire d'une autre table.", ["Vrai", "Faux"], 0),
                ("Quel mot-clé combine deux tables ?", ["UNION", "MERGE", "JOIN", "LINK"], 2),
            ],
        },
        {
            "titre": "Algorithmes de tri",
            "niveau": "avance",
            "mots_cles": "algorithmes, tri, complexité, python",
            "description": "Tri par sélection, tri fusion et complexité.",
            "texte": "Le tri par sélection cherche à chaque étape le plus petit élément restant : sa complexité est en "
                     "O(n²). Le tri fusion divise la liste en deux, trie chaque moitié puis fusionne : sa complexité est en "
                     "O(n log n). Un tri est stable s'il conserve l'ordre des éléments égaux.",
            "resume": "- Tri par sélection : O(n²).\n- Tri fusion : diviser, trier, fusionner, en O(n log n).\n"
                      "- Tri stable : conserve l'ordre des éléments égaux.",
            "questions": [
                ("Complexité du tri fusion ?", ["O(n)", "O(n²)", "O(n log n)", "O(log n)"], 2),
                ("Le tri par sélection est en O(n²).", ["Vrai", "Faux"], 0),
                ("Un tri stable :", ["est toujours en O(n)", "conserve l'ordre des éléments égaux",
                                     "ne fait aucune comparaison", "trie en place"], 1),
            ],
        },
    ],
    "Physique": [
        {
            "titre": "Lois de Newton",
            "niveau": "debutant",
            "mots_cles": "mécanique, forces, newton, dynamique",
            "description": "Principe d'inertie, principe fondamental de la dynamique, actions réciproques.",
            "texte": "Première loi : un corps isolé reste au repos ou en mouvement rectiligne uniforme. Deuxième loi : "
                     "la somme des forces est égale à la masse fois l'accélération, F = m·a. Troisième loi : si A exerce "
                     "une force sur B, B exerce sur A une force égale et opposée.",
            "resume": "- Inertie : sans force, repos ou mouvement rectiligne uniforme.\n- PFD : ΣF = m·a.\n"
                      "- Actions réciproques : forces égales et opposées.",
            "questions": [
                ("Quelle relation exprime la deuxième loi de Newton ?", ["E = mc²", "F = m·a", "P = U·I", "v = d/t"], 1),
                ("Un objet sans force qui s'exerce sur lui peut être en mouvement.", ["Vrai", "Faux"], 0),
                ("Unité de la force :", ["Joule", "Watt", "Newton", "Pascal"], 2),
            ],
        },
        {
            "titre": "Électricité : loi d'Ohm",
            "niveau": "debutant",
            "mots_cles": "électricité, tension, intensité, résistance",
            "description": "Tension, intensité, résistance et puissance électrique.",
            "texte": "La tension U se mesure en volts, l'intensité I en ampères et la résistance R en ohms. La loi d'Ohm "
                     "s'écrit U = R·I. La puissance électrique reçue par un dipôle vaut P = U·I et s'exprime en watts.",
            "resume": "- U en volts, I en ampères, R en ohms.\n- Loi d'Ohm : U = R·I.\n- Puissance : P = U·I, en watts.",
            "questions": [
                ("Loi d'Ohm :", ["U = R·I", "U = R/I", "P = R·I", "I = U·R"], 0),
                ("L'intensité se mesure en volts.", ["Vrai", "Faux"], 1),
                ("Puissance d'un dipôle sous 10 V traversé par 2 A ?", ["5 W", "12 W", "20 W", "8 W"], 2),
            ],
        },
        {
            "titre": "Ondes et lumière",
            "niveau": "intermediaire",
            "mots_cles": "ondes, lumière, longueur d'onde, fréquence",
            "description": "Périodicité, longueur d'onde, célérité et spectre lumineux.",
            "texte": "Une onde transporte de l'énergie sans transport de matière. La longueur d'onde λ, la célérité v et "
                     "la fréquence f sont liées par λ = v / f. Dans le vide, la lumière se propage à environ 3 × 10⁸ m/s. "
                     "La lumière visible correspond à des longueurs d'onde entre 400 et 800 nm.",
            "resume": "- Une onde transporte de l'énergie, pas de matière.\n- λ = v / f.\n"
                      "- Lumière dans le vide : 3 × 10⁸ m/s ; visible entre 400 et 800 nm.",
            "questions": [
                ("Relation entre longueur d'onde, célérité et fréquence ?", ["λ = v·f", "λ = v / f", "λ = f / v", "λ = v + f"], 1),
                ("Une onde transporte de la matière.", ["Vrai", "Faux"], 1),
                ("Vitesse de la lumière dans le vide :", ["340 m/s", "3 × 10⁵ m/s", "3 × 10⁸ m/s", "1 km/s"], 2),
            ],
        },
    ],
    "Anglais": [
        {
            "titre": "Present perfect vs past simple",
            "niveau": "intermediaire",
            "mots_cles": "anglais, grammaire, temps, present perfect",
            "description": "Choisir entre present perfect et past simple.",
            "texte": "Le past simple décrit une action terminée à un moment précis du passé : I visited London in 2020. "
                     "Le present perfect relie le passé au présent : I have visited London twice. On utilise souvent "
                     "for et since avec le present perfect, et yesterday, ago ou last year avec le past simple.",
            "resume": "- Past simple : action terminée à un moment précis (yesterday, ago, last year).\n"
                      "- Present perfect : lien avec le présent (for, since, ever, never).",
            "questions": [
                ("« I ___ to Paris last year. »", ["have gone", "went", "go", "have went"], 1),
                ("Le present perfect s'emploie avec « yesterday ».", ["Vrai", "Faux"], 1),
                ("« She ___ here since 2019. »", ["lives", "lived", "has lived", "is living"], 2),
            ],
        },
        {
            "titre": "Vocabulaire du monde professionnel",
            "niveau": "debutant",
            "mots_cles": "anglais, vocabulaire, entreprise, emploi",
            "description": "Mots essentiels pour un entretien et la vie en entreprise.",
            "texte": "Pour postuler, on envoie un CV (resume en anglais américain) et une lettre de motivation (cover "
                     "letter). Pendant l'entretien (job interview), on présente ses compétences (skills). Le salaire se dit "
                     "salary et un collègue se dit colleague ou coworker.",
            "resume": "- CV = resume (US) ; lettre de motivation = cover letter.\n- Entretien = job interview ; compétences = skills.\n"
                      "- Salaire = salary ; collègue = colleague / coworker.",
            "questions": [
                ("« Lettre de motivation » se dit :", ["cover letter", "motivation paper", "job letter", "skills letter"], 0),
                ("« Skills » signifie « compétences ».", ["Vrai", "Faux"], 0),
                ("« Entretien d'embauche » se dit :", ["job meeting", "job interview", "work talk", "hiring speech"], 1),
            ],
        },
        {
            "titre": "Rédiger un e-mail formel",
            "niveau": "avance",
            "mots_cles": "anglais, rédaction, e-mail, communication",
            "description": "Structure et formules d'un e-mail professionnel en anglais.",
            "texte": "Un e-mail formel commence par Dear Mr / Ms suivi du nom. On annonce l'objet dès la première phrase : "
                     "I am writing to... On évite les contractions. Pour conclure, on utilise Yours sincerely si l'on connaît "
                     "le nom du destinataire, Yours faithfully sinon, ou Best regards dans un contexte moins formel.",
            "resume": "- Ouverture : Dear Mr / Ms + nom.\n- Objet dès la première phrase : I am writing to...\n"
                      "- Pas de contractions.\n- Clôture : Yours sincerely, Yours faithfully ou Best regards.",
            "questions": [
                ("Formule d'ouverture formelle :", ["Hi there", "Dear Ms Smith", "Hey", "Yo"], 1),
                ("Les contractions (I'm, don't) sont recommandées dans un e-mail formel.", ["Vrai", "Faux"], 1),
                ("Si l'on ne connaît pas le nom du destinataire :", ["Yours faithfully", "Cheers", "Love", "See you"], 0),
            ],
        },
    ],
}

COMMENTAIRES_AVIS = {
    5: ["Très clair, merci !", "Excellent cours, les exemples aident beaucoup.", "Le résumé est parfait pour réviser."],
    4: ["Bien expliqué.", "Cours utile, un peu rapide sur la fin.", "Bon cours, j'aimerais plus d'exercices."],
    3: ["Correct mais un peu court.", "Quelques passages difficiles à suivre."],
    2: ["Pas assez d'exemples."],
}

# Échanges avec l'assistant : (question de l'étudiant, réponse de l'assistant), par matière
ECHANGES_ASSISTANT = {
    "Mathématiques": [("Pourquoi le produit de matrices n'est pas commutatif ?",
                       "Parce que AB combine les lignes de A avec les colonnes de B : en inversant l'ordre, on combine "
                       "d'autres lignes et colonnes. Par exemple, avec A = [[1, 1], [0, 0]] et B = [[1, 0], [1, 0]], "
                       "AB ≠ BA.")],
    "Informatique": [("Quelle est la différence entre une boucle for et une boucle while ?",
                      "for parcourt une séquence connue (une liste, un range) ; while répète tant qu'une condition est "
                      "vraie, quand on ne connaît pas le nombre de tours à l'avance.")],
    "Physique": [("Comment retrouver la puissance avec la loi d'Ohm ?",
                  "P = U·I et U = R·I, donc P = R·I² ou encore P = U² / R.")],
    "Anglais": [("Quand utiliser since et for ?",
                 "since + point de départ (since 2019, since Monday) ; for + durée (for three years, for two hours). "
                 "Les deux s'emploient avec le present perfect.")],
}
