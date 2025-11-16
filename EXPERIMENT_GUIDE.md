# Guida agli Esperimenti di Performance

Questa guida spiega come utilizzare il sistema di automazione per testare le prestazioni dei microservizi su Kubernetes.

## 📋 Prerequisiti

### Software Richiesto
- Python 3.8+
- Kubernetes cluster funzionante (minikube, kind, o cluster remoto)
- kubectl configurato
- Istio service mesh installato
- Prometheus deployato (con accesso via port-forward o service)
- Locust

### Installazione Dipendenze Python

```bash
pip install -r requirements.txt
```

## 🚀 Setup Iniziale

### 1. Verifica Connessione Kubernetes

```bash
kubectl cluster-info
kubectl get nodes
```

### 2. Configura Port-Forward per Prometheus (se necessario)

```bash
# Se Prometheus è in K8s, esponi il servizio localmente
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090
```

### 3. Identifica l'Ingress Gateway

```bash
# Per minikube
export LOCUST_HOST="http://$(minikube ip)"

# Per cluster con LoadBalancer
export LOCUST_HOST="http://$(kubectl get svc istio-ingressgateway -n istio-system -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"

# Per port-forward locale
kubectl port-forward -n istio-system svc/istio-ingressgateway 8080:80
export LOCUST_HOST="http://localhost:8080"
```

## 📊 Modalità di Test

### Modalità 1: Load Costante

Genera un numero fisso di utenti concorrenti per tutta la durata del test.

**Esempio:**

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant \
  --users 100 \
  --think-time 0.5 \
  --duration 300 \
  --locust-host $LOCUST_HOST \
  --prometheus-url http://localhost:9090 \
  --output-dir results/constant_100users
```

**Parametri:**
- `--users`: Numero di utenti concorrenti (es. 50, 100, 200)
- `--think-time`: Tempo di attesa tra richieste in secondi (es. 0.1, 0.5, 1.0)
- `--duration`: Durata del test in secondi (es. 300 = 5 minuti)

### Modalità 2: Load Basato su Tracce

Utilizza tracce reali di carico per simulare pattern più realistici.

**Esempio con traccia sinusoidale:**

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode trace \
  --trace-file QNonK8s/locust/workloads/sin800.csv \
  --amplitude 50 \
  --shift 10 \
  --duration 600 \
  --trace-think-time 0.0 \
  --locust-host $LOCUST_HOST \
  --prometheus-url http://localhost:9090 \
  --output-dir results/trace_sin800
```

**Esempio con traccia Twitter:**

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode trace \
  --trace-file QNonK8s/locust/workloads/twitter.csv \
  --amplitude 100 \
  --shift 20 \
  --duration 600 \
  --fit-trace \
  --locust-host $LOCUST_HOST \
  --output-dir results/trace_twitter
```

**Parametri:**
- `--trace-file`: File CSV con la traccia di carico
- `--amplitude`: Numero massimo di utenti sopra la baseline
- `--shift`: Numero minimo di utenti (baseline)
- `--trace-think-time`: Tempo di attesa tra richieste
- `--fit-trace`: Adatta la traccia alla durata specificata

## 🎯 Esempi di Scenari Didattici

### Scenario 1: Studio dell'Utilizzo della CPU

Testa come varia l'utilizzo della CPU con diversi carichi:

```bash
# Test 1: 50 utenti
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant --users 50 --think-time 0.5 --duration 180 \
  --locust-host $LOCUST_HOST --output-dir results/cpu_study/50users

# Test 2: 100 utenti
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant --users 100 --think-time 0.5 --duration 180 \
  --locust-host $LOCUST_HOST --output-dir results/cpu_study/100users

# Test 3: 200 utenti
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant --users 200 --think-time 0.5 --duration 180 \
  --locust-host $LOCUST_HOST --output-dir results/cpu_study/200users
```

### Scenario 2: Analisi del Throughput

Misura il throughput massimo del sistema:

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant \
  --users 500 \
  --think-time 0.01 \
  --duration 300 \
  --locust-host $LOCUST_HOST \
  --output-dir results/max_throughput
```

### Scenario 3: Confronto Architetture

Testa deployment diversi con lo stesso carico:

```bash
# Test deployment standard (1 replica)
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode trace --trace-file QNonK8s/locust/workloads/sin800.csv \
  --amplitude 50 --shift 10 --duration 300 \
  --locust-host $LOCUST_HOST \
  --output-dir results/architecture_comparison/1replica

# Modifica il manifest per avere 2 repliche, poi:
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier_2replicas.yaml \
  --mode trace --trace-file QNonK8s/locust/workloads/sin800.csv \
  --amplitude 50 --shift 10 --duration 300 \
  --locust-host $LOCUST_HOST \
  --output-dir results/architecture_comparison/2replicas
```

### Scenario 4: Workload Realistico (Twitter Trace)

Simula un carico realistico basato su dati Twitter:

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode trace \
  --trace-file QNonK8s/locust/workloads/twitter.csv \
  --amplitude 150 \
  --shift 30 \
  --duration 600 \
  --fit-trace \
  --locust-host $LOCUST_HOST \
  --output-dir results/realistic_workload
```

## 📈 Output dell'Esperimento

Ogni esperimento genera:

### 1. File CSV con Metriche

**`metrics_YYYYMMDD_HHMMSS.csv`** contiene:
- `timestamp`: Timestamp Unix
- `elapsed`: Secondi dall'inizio
- `throughput`: Richieste/secondo (Istio)
- `response_time_avg`: Tempo di risposta medio (ms)
- `cpu_usage_task1`: Utilizzo CPU Task1 (cores)
- `cpu_usage_task2`: Utilizzo CPU Task2 (cores)
- `throughput_task1`: Throughput Task1
- `throughput_task2`: Throughput Task2
- `response_time_task1`: Response time Task1
- `response_time_task2`: Response time Task2
- `locust_users`: Utenti attivi Locust
- `locust_requests_per_second`: RPS cliente
- `locust_response_time_avg`: Response time cliente (ms)
- `locust_failure_rate`: Tasso di fallimento (0-1)

**`replicas_YYYYMMDD_HHMMSS.csv`** contiene:
- `timestamp`: Timestamp Unix
- `elapsed`: Secondi dall'inizio
- `deployment`: Nome deployment
- `desired`: Repliche desiderate
- `ready`: Repliche pronte
- `available`: Repliche disponibili

### 2. Grafici PNG

**`plots_YYYYMMDD_HHMMSS.png`** contiene 4 plot:
1. **Throughput nel tempo**: Server vs Client
2. **Response Time nel tempo**: Server vs Client
3. **CPU Usage nel tempo**: Task1 e Task2
4. **Utenti Attivi nel tempo**: Profilo di carico

### 3. File CSV di Locust

- `locust_YYYYMMDD_HHMMSS_stats.csv`: Statistiche richieste
- `locust_YYYYMMDD_HHMMSS_stats_history.csv`: Storia delle statistiche
- `locust_YYYYMMDD_HHMMSS_failures.csv`: Fallimenti

## 🔧 Opzioni Avanzate

### Non Effettuare Cleanup

Per mantenere il deployment dopo il test (utile per debug):

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant --users 50 --think-time 0.5 --duration 60 \
  --locust-host $LOCUST_HOST \
  --no-cleanup
```

Ricorda di fare cleanup manuale:

```bash
kubectl delete -f QNonK8s/applications/3tier.yaml
```

### Intervallo di Raccolta Metriche

Per raccogliere metriche più frequentemente (default: 5s):

```bash
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --mode constant --users 100 --think-time 0.5 --duration 300 \
  --locust-host $LOCUST_HOST \
  --metrics-interval 2
```

### Namespace Personalizzato

```bash
# Prima crea il namespace
kubectl create namespace performance-tests

# Poi esegui il test
python experiment_runner.py \
  --manifest QNonK8s/applications/3tier.yaml \
  --namespace performance-tests \
  --mode constant --users 50 --think-time 0.5 --duration 180 \
  --locust-host $LOCUST_HOST
```

## 📊 Analisi dei Risultati

### Importare i CSV in Python

```python
import pandas as pd
import matplotlib.pyplot as plt

# Carica i dati
metrics = pd.read_csv('results/metrics_20241116_143022.csv')
replicas = pd.read_csv('results/replicas_20241116_143022.csv')

# Analisi di base
print(metrics.describe())

# Calcola throughput medio
avg_throughput = metrics['throughput'].mean()
print(f"Throughput medio: {avg_throughput:.2f} req/s")

# Calcola response time medio
avg_rt = metrics['response_time_avg'].mean()
print(f"Response time medio: {avg_rt:.2f} ms")

# Calcola utilizzo CPU medio
avg_cpu_task1 = metrics['cpu_usage_task1'].mean()
avg_cpu_task2 = metrics['cpu_usage_task2'].mean()
print(f"CPU Task1 medio: {avg_cpu_task1:.3f} cores")
print(f"CPU Task2 medio: {avg_cpu_task2:.3f} cores")
```

### Confrontare Esperimenti

```python
import pandas as pd
import matplotlib.pyplot as plt

# Carica due esperimenti
exp1 = pd.read_csv('results/cpu_study/50users/metrics_*.csv')
exp2 = pd.read_csv('results/cpu_study/100users/metrics_*.csv')

# Confronta throughput
plt.figure(figsize=(12, 6))
plt.plot(exp1['elapsed'], exp1['throughput'], label='50 users')
plt.plot(exp2['elapsed'], exp2['throughput'], label='100 users')
plt.xlabel('Time (s)')
plt.ylabel('Throughput (req/s)')
plt.title('Throughput Comparison')
plt.legend()
plt.grid(True)
plt.show()
```

## 🐛 Troubleshooting

### Problema: Prometheus non raggiungibile

```bash
# Verifica che Prometheus sia in esecuzione
kubectl get pods -n istio-system | grep prometheus

# Verifica port-forward
kubectl port-forward -n istio-system svc/prometheus 9090:9090

# Testa connessione
curl http://localhost:9090/api/v1/query?query=up
```

### Problema: Locust non trova il target

```bash
# Verifica ingress gateway
kubectl get svc -n istio-system istio-ingressgateway

# Testa connessione diretta
curl $LOCUST_HOST
```

### Problema: Nessuna metrica Istio

Verifica che i pod abbiano il sidecar Istio:

```bash
kubectl get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[*].name}{"\n"}'
```

Dovresti vedere `istio-proxy` tra i container.

### Problema: Metriche Locust mancanti

Verifica che l'exporter sia attivo:

```bash
# Durante l'esecuzione di Locust
curl http://localhost:9646/metrics
```

## 📚 Riferimenti

- **JMT (Java Modelling Tools)**: http://jmt.sourceforge.net/
- **Locust Documentation**: https://docs.locust.io/
- **Istio Metrics**: https://istio.io/latest/docs/reference/config/metrics/
- **Prometheus Query Language**: https://prometheus.io/docs/prometheus/latest/querying/basics/

## 💡 Suggerimenti per gli Studenti

1. **Inizia con test brevi**: Usa `--duration 60` per test rapidi durante la fase di setup
2. **Verifica i log**: Controlla l'output della console per eventuali warning
3. **Confronta modalità**: Prova sia constant che trace per capire le differenze
4. **Analizza i trend**: Cerca correlazioni tra carico, CPU e response time
5. **Documenta gli esperimenti**: Prendi nota dei parametri e dei risultati
6. **Usa git**: Versiona i manifest e i risultati degli esperimenti

## 🎓 Esercizi Proposti

1. **Saturazione CPU**: Trova il carico che satura la CPU al 100%
2. **Scalabilità**: Testa con 1, 2, 3 repliche e confronta i risultati
3. **Legge di Little**: Verifica N = X * R usando i dati raccolti
4. **Modello M/M/1**: Confronta previsioni teoriche con misure reali
5. **Latency Analysis**: Studia come varia il percentile 95° del response time
