// =====================================================================
// Pipeline CI/CD d'EduVox (Django + Celery, une seule image Docker)
//   Image -> tests et couverture -> sécurité -> SonarQube -> Nexus -> Kubernetes
// Même infrastructure que le projet RH : Jenkins + SonarQube + Nexus (VM outils)
// et cluster Kubernetes (master + 2 workers) avec stockage NFS et Ingress Nginx.
// =====================================================================

/** Vérifie si SonarQube répond. */
def sonarDisponible(String url) {
    return sh(script: "curl -sf ${url}/api/system/status | grep -q '\"status\":\"UP\"'",
              returnStatus: true) == 0
}

pipeline {
    agent any

    options {
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '10', artifactNumToKeepStr: '3'))
        disableConcurrentBuilds()
        timeout(time: 120, unit: 'MINUTES')
    }

    parameters {
        booleanParam(name: 'DEPLOYER', defaultValue: true,
                     description: 'Déployer sur Kubernetes si tout est vert')
    }

    triggers {
        pollSCM('H/5 * * * *')
    }

    environment {
        REGISTRY        = '192.168.255.129:8082'
        IMAGE           = "${REGISTRY}/eduvox"
        TEST_CONTAINER  = "eduvox-tests-${env.BUILD_NUMBER}"
        SONAR_URL       = 'http://192.168.255.129:9000'
        NAMESPACE       = 'eduvox'
        DOCKER_BUILDKIT = '1'
    }

    stages {

        stage('Préparation') {
            steps {
                script {
                    // Version = numéro du build + commit Git : chaque image est traçable jusqu'au code
                    env.IMAGE_TAG = env.GIT_COMMIT ? "${env.BUILD_NUMBER}-${env.GIT_COMMIT.take(7)}" : "${env.BUILD_NUMBER}"
                    env.SONAR_UP  = sonarDisponible(env.SONAR_URL) ? 'true' : 'false'
                    echo "Version de l'image : ${env.IMAGE_TAG}"
                    echo(env.SONAR_UP == 'true' ? 'SonarQube disponible' : 'SonarQube arrêté : analyse ignorée')
                }
            }
        }

        stage('Image Docker') {
            steps {
                sh 'docker build -t "$IMAGE:$IMAGE_TAG" -t "$IMAGE:latest" .'
            }
        }

        stage('Tests et couverture') {
            steps {
                // Tests dans l'image : base SQLite temporaire, tâches Celery exécutées directement,
                // aucun accès à la base Neon ni aux services d'IA payants.
                sh '''
                    docker run --name "$TEST_CONTAINER" \
                      -e DEBUG=True \
                      -e SECRET_KEY=cle-de-test-jenkins \
                      -e CELERY_TASK_ALWAYS_EAGER=True \
                      -e CELERY_BROKER_URL=memory:// \
                      -e TEST_OUTPUT_DIR=/tmp/rapports \
                      -e GEMINI_API_KEY= -e HF_TOKEN= \
                      "$IMAGE:$IMAGE_TAG" \
                      sh -c "pip install -q --no-cache-dir --user unittest-xml-reporting \
                             && python -m coverage run --source=apps,ai_services,config \
                                  --omit='*/migrations/*,*/tests.py,*/tests/*,*/management/commands/modeles_visage.py' \
                                  manage.py test --noinput --testrunner xmlrunner.extra.djangotestrunner.XMLTestRunner \
                             && python -m coverage xml -o /tmp/rapports/coverage.xml \
                             && python -m coverage report --skip-covered | tail -25"
                '''
            }
            post {
                always {
                    sh '''
                        rm -rf rapports && mkdir -p rapports
                        docker cp "$TEST_CONTAINER:/tmp/rapports/." rapports/ || true
                        docker rm -f "$TEST_CONTAINER" || true
                        # Chemins du conteneur de test (/app) -> chemins vus par le scanner SonarQube (/usr/src)
                        sed -i 's#<source>/app#<source>/usr/src#' rapports/coverage.xml || true
                    '''
                    junit allowEmptyResults: true, testResults: 'rapports/*.xml'
                }
            }
        }

        stage('Contrôles de sécurité') {
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                    // 1. Réglages Django de production (HTTPS, cookies, HSTS, DEBUG…)
                    // 2. Bandit : failles courantes dans le code Python
                    // 3. pip-audit : dépendances avec des vulnérabilités connues
                    sh '''
                        docker run --rm \
                          -e DEBUG=False -e SECRET_KEY="$(head -c 48 /dev/urandom | base64)" \
                          -e ALLOWED_HOSTS=192.168.255.136 -e CSRF_TRUSTED_ORIGINS=https://192.168.255.136:30443 \
                          -e SECURE_HSTS_INCLUDE_SUBDOMAINS=True \
                          "$IMAGE:$IMAGE_TAG" python manage.py check --deploy --fail-level WARNING

                        docker run --rm "$IMAGE:$IMAGE_TAG" sh -c \
                          "pip install -q --no-cache-dir --user bandit && python -m bandit -q -r apps ai_services config -x '*/tests.py,*/migrations/*' -ll"

                        docker run --rm "$IMAGE:$IMAGE_TAG" sh -c \
                          "pip install -q --no-cache-dir --user pip-audit && python -m pip_audit --progress-spinner off"
                    '''
                }
            }
        }

        stage('Analyse SonarQube') {
            when { environment name: 'SONAR_UP', value: 'true' }
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    withSonarQubeEnv('sonarqube') {
                        sh '''
                            docker run --rm \
                              --memory 2g \
                              -u "$(id -u):$(id -g)" \
                              -e SONAR_HOST_URL="$SONAR_HOST_URL" \
                              -e SONAR_TOKEN="$SONAR_AUTH_TOKEN" \
                              -e SONAR_USER_HOME=/usr/src/.sonar \
                              -e SONAR_SCANNER_OPTS="-Xmx512m" \
                              -v "$WORKSPACE:/usr/src" \
                              -w /usr/src \
                              sonarsource/sonar-scanner-cli \
                              -Dsonar.working.directory=/usr/src/.scannerwork \
                              -Dsonar.python.coverage.reportPaths=rapports/coverage.xml
                        '''
                    }
                    script { env.ANALYSE_OK = 'true' }
                }
            }
        }

        stage('Quality Gate') {
            when { environment name: 'ANALYSE_OK', value: 'true' }
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    timeout(time: 5, unit: 'MINUTES') {
                        waitForQualityGate abortPipeline: false
                    }
                }
            }
        }

        stage('Envoi vers Nexus') {
            steps {
                withCredentials([usernamePassword(credentialsId: 'nexus-docker',
                                                  usernameVariable: 'NEXUS_USER',
                                                  passwordVariable: 'NEXUS_PWD')]) {
                    sh '''
                        echo "$NEXUS_PWD" | docker login "$REGISTRY" -u "$NEXUS_USER" --password-stdin
                        docker push -q "$IMAGE:$IMAGE_TAG"
                        docker push -q "$IMAGE:latest"
                    '''
                }
            }
        }

        stage('Déploiement Kubernetes') {
            when { expression { params.DEPLOYER } }
            steps {
                withCredentials([file(credentialsId: 'kubeconfig', variable: 'KUBECONFIG')]) {
                    sh '''
                        # Manifestes à jour, avec la version exacte de l'image à la place de « latest »
                        # (le Secret eduvox-secrets et le certificat eduvox-tls sont créés à la main, une seule fois)
                        for f in k8s/*.yaml; do
                            sed "s#$IMAGE:latest#$IMAGE:$IMAGE_TAG#g" "$f"
                            echo "---"
                        done | kubectl apply -f -

                        echec=0
                        kubectl -n "$NAMESPACE" rollout status deployment/eduvox-web --timeout=600s || echec=1
                        kubectl -n "$NAMESPACE" rollout status deployment/eduvox-worker --timeout=600s || echec=1
                        if [ "$echec" = "1" ]; then
                            echo "Échec du déploiement : retour à la version précédente"
                            kubectl -n "$NAMESPACE" rollout undo deployment/eduvox-web || true
                            kubectl -n "$NAMESPACE" rollout undo deployment/eduvox-worker || true
                            exit 1
                        fi
                    '''
                }
            }
        }
    }

    post {
        always {
            sh 'docker logout "$REGISTRY" || true'
            sh 'docker rmi "$IMAGE:$IMAGE_TAG" "$IMAGE:latest" 2>/dev/null || true'
        }
        success {
            echo "EduVox livré en version ${env.IMAGE_TAG}"
        }
    }
}