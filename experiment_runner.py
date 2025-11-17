#!/usr/bin/env python3
"""
Kubernetes Performance Testing Experiment Runner

This script automates the deployment, load testing, and metrics collection
for Kubernetes microservices. It orchestrates kubectl deployments, Locust
load generation, and Prometheus metrics collection.

Usage:
    # Constant load test
    python experiment_runner.py \\
        --manifest QNonK8s/applications/3tier.yaml \\
        --mode constant \\
        --users 100 \\
        --think-time 0.5 \\
        --duration 300

    # Trace-based load test
    python experiment_runner.py \\
        --manifest QNonK8s/applications/3tier.yaml \\
        --mode trace \\
        --trace-file QNonK8s/locust/workloads/sin800.csv \\
        --amplitude 50 \\
        --shift 10 \\
        --duration 300
"""

import argparse
import subprocess
import time
import os
import sys
import signal
import math
import yaml
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

# Import the metrics collector
from metrics_collector import PrometheusCollector

# Import JMT validator (optional)
try:
    from jmt_validator import JMTValidator
    JMT_AVAILABLE = True
except ImportError:
    JMT_AVAILABLE = False


class ExperimentRunner:
    """
    Orchestrates Kubernetes deployment, load testing, and metrics collection.
    """

    def __init__(
        self,
        manifest_path: str,
        namespace: str = "default",
        prometheus_url: str = "http://localhost:9090",
        output_dir: str = "results",
        locust_host: Optional[str] = None,
        cleanup: bool = True,
        auto_detect_ingress: bool = True,
        auto_port_forward: bool = True,
        config_path: Optional[str] = None,
        gateway_manifest_path: Optional[str] = None
    ):
        """
        Initialize the experiment runner.

        Args:
            manifest_path: Path to Kubernetes manifest YAML file
            namespace: Kubernetes namespace for deployment
            prometheus_url: Prometheus server URL
            output_dir: Directory to save results
            locust_host: Target host for Locust load testing (auto-detected if None)
            cleanup: Whether to cleanup deployments after test
            auto_detect_ingress: Automatically detect Istio Ingress Gateway URL
            auto_port_forward: Automatically setup Prometheus port-forward
            config_path: Path to experiment_mapping.yaml (for parametric queries)
            gateway_manifest_path: Path to Istio Gateway/VirtualService manifest (auto-applied if needed)
        """
        self.manifest_path = Path(manifest_path)
        self.namespace = namespace
        self.prometheus_url = prometheus_url
        self.output_dir = Path(output_dir)
        self.cleanup = cleanup
        self.auto_port_forward = auto_port_forward
        self.gateway_manifest_path = Path(gateway_manifest_path) if gateway_manifest_path else None

        # Load experiment mapping config if provided
        self.deployments_map = {}
        if config_path:
            self.deployments_map = self._load_deployment_mapping(config_path)

        # Auto-detect Istio Ingress Gateway if not provided
        if locust_host:
            self.locust_host = locust_host
        elif auto_detect_ingress:
            self.locust_host = self._detect_istio_ingress_gateway()
        else:
            self.locust_host = "http://localhost:80"

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.locust_process = None
        self.port_forward_process = None
        self.collector = None

        # Generate experiment ID and create timestamped subdirectory
        self.experiment_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.experiment_output_dir = self.output_dir / self.experiment_id
        self.experiment_output_dir.mkdir(parents=True, exist_ok=True)

    def _detect_istio_ingress_gateway(self) -> str:
        """
        Automatically detect the Istio Ingress Gateway URL.
        If Gateway doesn't exist and gateway_manifest_path is configured,
        it will automatically apply the Gateway manifest.

        Returns:
            The URL of the Istio Ingress Gateway

        Raises:
            RuntimeError: If Gateway is not found and cannot be applied
        """
        print("🔍 Auto-detecting Istio Ingress Gateway...")

        # Ensure Minikube addons are enabled (if using Minikube)
        try:
            subprocess.check_output(['minikube', 'status'], timeout=5, stderr=subprocess.DEVNULL)
            # Minikube is running, ensure addons are enabled
            self._ensure_minikube_addons()
        except (subprocess.SubprocessError, FileNotFoundError):
            # Not using Minikube or minikube not available
            pass

        # Ensure Prometheus is installed
        self._ensure_prometheus_installed()

        # First, ensure Gateway exists (apply if needed)
        self._apply_istio_gateway()

        try:
            # Try minikube first
            try:
                minikube_ip = subprocess.check_output(
                    ['minikube', 'ip'],
                    text=True,
                    timeout=10,
                    stderr=subprocess.DEVNULL
                ).strip()

                # Get NodePort
                nodeport = subprocess.check_output(
                    ['kubectl', 'get', 'svc', 'istio-ingressgateway', '-n', 'istio-system',
                     '-o', 'jsonpath={.spec.ports[?(@.name=="http2")].nodePort}'],
                    text=True,
                    timeout=10
                ).strip()

                if minikube_ip and nodeport:
                    url = f"http://{minikube_ip}:{nodeport}"
                    print(f"✅ Detected Minikube Ingress Gateway: {url}")
                    return url
            except (subprocess.SubprocessError, FileNotFoundError):
                pass

            # Try LoadBalancer external IP
            try:
                external_ip = subprocess.check_output(
                    ['kubectl', 'get', 'svc', 'istio-ingressgateway', '-n', 'istio-system',
                     '-o', 'jsonpath={.status.loadBalancer.ingress[0].ip}'],
                    text=True,
                    timeout=10
                ).strip()

                if external_ip:
                    url = f"http://{external_ip}"
                    print(f"✅ Detected LoadBalancer Ingress Gateway: {url}")
                    return url
            except subprocess.SubprocessError:
                pass

            # Try NodePort with first node IP
            try:
                node_ip = subprocess.check_output(
                    ['kubectl', 'get', 'nodes', '-o',
                     'jsonpath={.items[0].status.addresses[?(@.type=="InternalIP")].address}'],
                    text=True,
                    timeout=10
                ).strip()

                nodeport = subprocess.check_output(
                    ['kubectl', 'get', 'svc', 'istio-ingressgateway', '-n', 'istio-system',
                     '-o', 'jsonpath={.spec.ports[?(@.name=="http2")].nodePort}'],
                    text=True,
                    timeout=10
                ).strip()

                if node_ip and nodeport:
                    url = f"http://{node_ip}:{nodeport}"
                    print(f"✅ Detected NodePort Ingress Gateway: {url}")
                    return url
            except subprocess.SubprocessError:
                pass

            print("⚠️  Could not auto-detect Ingress Gateway, using default localhost:80")
            return "http://localhost:80"

        except Exception as e:
            print(f"⚠️  Error detecting Ingress Gateway: {e}")
            return "http://localhost:80"

    def _check_istio_gateway_exists(self) -> bool:
        """
        Check if Istio Gateway resource exists in the cluster.

        Returns:
            True if Gateway exists, False otherwise
        """
        try:
            result = subprocess.run(
                ['kubectl', 'get', 'gateway', '-n', self.namespace, '-o', 'name'],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0 and result.stdout.strip() != ""
        except Exception:
            return False

    def _apply_istio_gateway(self) -> bool:
        """
        Apply Istio Gateway and VirtualService manifests if they don't exist.

        Returns:
            True if Gateway is available (existing or newly applied), False on error

        Raises:
            RuntimeError: If gateway manifest is not configured and gateway doesn't exist
        """
        # Check if Gateway already exists
        if self._check_istio_gateway_exists():
            print("✅ Istio Gateway already exists")
            return True

        print("⚠️  Istio Gateway not found in cluster")

        # Check if gateway manifest path is configured
        if not self.gateway_manifest_path:
            raise RuntimeError(
                "Istio Gateway not found and no gateway manifest configured. "
                "Please provide --gateway-manifest parameter or apply the gateway manually:\n"
                "  kubectl apply -f QNonK8s/istio/gateway.yaml"
            )

        # Check if manifest file exists
        if not self.gateway_manifest_path.exists():
            raise RuntimeError(
                f"Gateway manifest file not found: {self.gateway_manifest_path}\n"
                "Please provide a valid path to the Istio Gateway manifest."
            )

        print(f"📦 Applying Istio Gateway manifest: {self.gateway_manifest_path}")

        try:
            result = subprocess.run(
                ['kubectl', 'apply', '-f', str(self.gateway_manifest_path), '-n', self.namespace],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to apply Istio Gateway manifest:\n{result.stderr}"
                )

            print(f"✅ Istio Gateway applied successfully")
            print(result.stdout)

            # Wait a moment for the gateway to be ready
            time.sleep(2)

            # Verify Gateway was created
            if not self._check_istio_gateway_exists():
                raise RuntimeError(
                    "Gateway manifest applied but Gateway resource not found. "
                    "Check that the manifest contains a valid Gateway resource."
                )

            # Verify VirtualService exists
            try:
                vs_result = subprocess.run(
                    ['kubectl', 'get', 'virtualservice', '-n', self.namespace, '-o', 'name'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if vs_result.returncode == 0 and vs_result.stdout.strip():
                    print("✅ VirtualService configured")
                else:
                    print("⚠️  Warning: VirtualService not found. Traffic routing may not work correctly.")
            except Exception as e:
                print(f"⚠️  Warning: Could not verify VirtualService: {e}")

            return True

        except subprocess.TimeoutExpired:
            raise RuntimeError("Timeout applying Istio Gateway manifest")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Error applying Istio Gateway: {e}")

    def _ensure_minikube_addons(self):
        """
        Ensure required Minikube addons are enabled.
        Re-applies addon enable commands with warning messages.
        """
        print("⚠️  Ensuring Minikube addons are enabled...")

        addons = [
            'istio-provisioner',
            'istio',
            'metrics-server'
        ]

        for addon in addons:
            try:
                print(f"  → Enabling {addon}...")
                result = subprocess.run(
                    ['minikube', 'addons', 'enable', addon],
                    capture_output=True,
                    text=True,
                    timeout=60
                )

                if result.returncode != 0:
                    print(f"    ⚠️  Warning: Failed to enable {addon}: {result.stderr.strip()}")
                else:
                    # Check if already enabled or newly enabled
                    if "already enabled" in result.stdout.lower() or "is already enabled" in result.stdout.lower():
                        print(f"    ✓ {addon} already enabled")
                    else:
                        print(f"    ✓ {addon} enabled")

            except subprocess.TimeoutExpired:
                print(f"    ⚠️  Warning: Timeout enabling {addon}")
            except FileNotFoundError:
                print(f"    ⚠️  Warning: minikube command not found, skipping addon setup")
                return
            except Exception as e:
                print(f"    ⚠️  Warning: Error enabling {addon}: {e}")

        # Enable Istio injection on default namespace
        try:
            print(f"  → Enabling Istio injection on namespace '{self.namespace}'...")
            result = subprocess.run(
                ['kubectl', 'label', 'namespace', self.namespace,
                 'istio-injection=enabled', '--overwrite'],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                print(f"    ✓ Istio injection enabled on namespace '{self.namespace}'")
            else:
                print(f"    ⚠️  Warning: Failed to enable Istio injection: {result.stderr.strip()}")
        except Exception as e:
            print(f"    ⚠️  Warning: Error enabling Istio injection: {e}")

        print("✅ Minikube addons configured")

    def _ensure_prometheus_installed(self):
        """
        Ensure Prometheus (kube-prometheus-stack) is installed via Helm.
        If not installed, installs it with Istio integration.
        """
        print("🔍 Checking Prometheus installation...")

        try:
            # Check if prometheus release exists in monitoring namespace
            result = subprocess.run(
                ['helm', 'status', 'prometheus', '-n', 'monitoring'],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                print("✅ Prometheus release already installed")
                return True

            # Release doesn't exist, install it
            print("⚠️  Prometheus not found, installing kube-prometheus-stack...")

            # Check if values file exists
            values_file = Path("QNonK8s/istio/prometheus-istio-values.yaml")
            if not values_file.exists():
                print(f"❌ Values file not found: {values_file}")
                raise RuntimeError(
                    f"Prometheus values file not found: {values_file}\n"
                    "Please ensure QNonK8s/istio/prometheus-istio-values.yaml exists."
                )

            # Install prometheus
            install_cmd = [
                'helm', 'install', 'prometheus',
                'prometheus-community/kube-prometheus-stack',
                '--namespace', 'monitoring',
                '-f', str(values_file),
                '--create-namespace'
            ]

            print(f"  → Running: {' '.join(install_cmd)}")

            install_result = subprocess.run(
                install_cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes timeout for installation
            )

            if install_result.returncode != 0:
                # Check if it's because repo is not added
                if "failed to fetch" in install_result.stderr.lower() or "not found" in install_result.stderr.lower():
                    print("  ⚠️  Helm repo not found, adding prometheus-community repo...")

                    # Add helm repo
                    add_repo_result = subprocess.run(
                        ['helm', 'repo', 'add', 'prometheus-community',
                         'https://prometheus-community.github.io/helm-charts'],
                        capture_output=True,
                        text=True,
                        timeout=60
                    )

                    if add_repo_result.returncode != 0:
                        raise RuntimeError(f"Failed to add Helm repo: {add_repo_result.stderr}")

                    print("  ✓ Helm repo added")

                    # Update repos
                    subprocess.run(['helm', 'repo', 'update'], capture_output=True, timeout=60)
                    print("  ✓ Helm repos updated")

                    # Retry installation
                    install_result = subprocess.run(
                        install_cmd,
                        capture_output=True,
                        text=True,
                        timeout=300
                    )

                    if install_result.returncode != 0:
                        raise RuntimeError(f"Failed to install Prometheus:\n{install_result.stderr}")
                else:
                    raise RuntimeError(f"Failed to install Prometheus:\n{install_result.stderr}")

            print("✅ Prometheus installed successfully")
            print(install_result.stdout)

            # Wait for Prometheus pods to be ready
            print("  ⏳ Waiting for Prometheus pods to be ready...")
            time.sleep(10)

            return True

        except subprocess.TimeoutExpired:
            raise RuntimeError("Timeout while installing Prometheus")
        except FileNotFoundError:
            raise RuntimeError(
                "Helm command not found. Please install Helm:\n"
                "  https://helm.sh/docs/intro/install/"
            )
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Error ensuring Prometheus installation: {e}")

    def _load_deployment_mapping(self, config_path: str) -> Dict[str, Dict]:
        """
        Load deployment mapping from experiment_mapping.yaml.

        Args:
            config_path: Path to experiment_mapping.yaml

        Returns:
            Dictionary mapping deployment names to their config:
            {'task1-deployment': {'service': 'task1-svc', 'task': 'task1', 'jmt_station': 'Tier1'}, ...}
        """
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)

            deployments_map = {}
            mapping = config.get('mapping', {})

            for deployment_key, deployment_config in mapping.items():
                # Skip delay station (not a real deployment)
                if deployment_key == 'delay':
                    continue

                # Extract deployment name, service name, and task name
                # deployment_key is like "task1-deployment"
                # service name is like "task1-svc"
                # task name is like "task1"
                task_name = deployment_key.replace('-deployment', '')
                service_name = f"{task_name}-svc"

                deployments_map[deployment_key] = {
                    'service': service_name,
                    'task': task_name,
                    'jmt_station': deployment_config.get('jmt_station', '')
                }

            print(f"✅ Loaded deployment mapping for {len(deployments_map)} deployments")
            return deployments_map

        except Exception as e:
            print(f"⚠️  Error loading deployment mapping: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def start_prometheus_port_forward(self) -> bool:
        """
        Start kubectl port-forward for Prometheus.

        Returns:
            True if port-forward started successfully or not needed
        """
        if not self.auto_port_forward:
            return True

        print(f"\n{'='*60}")
        print(f"🔌 Setting up Prometheus port-forward")
        print(f"{'='*60}")

        # Check if port 9090 is already in use
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('localhost', 9090))
        sock.close()

        if result == 0:
            print("✅ Port 9090 already in use (assuming Prometheus is accessible)")
            return True

        try:
            # Start port-forward in background
            cmd = [
                'kubectl', 'port-forward',
                '-n', 'monitoring',
                'svc/prometheus-kube-prometheus-prometheus',
                '9090:9090'
            ]

            print(f"  Starting: {' '.join(cmd)}")

            self.port_forward_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # Wait a moment for port-forward to establish
            time.sleep(3)

            # Verify it's running
            if self.port_forward_process.poll() is not None:
                stderr = self.port_forward_process.stderr.read()
                print(f"❌ Port-forward failed to start: {stderr}")
                return False

            # Test connection
            try:
                import requests
                response = requests.get('http://localhost:9090/api/v1/query?query=up', timeout=5)
                if response.status_code == 200:
                    print("✅ Prometheus port-forward established successfully")
                    return True
            except Exception:
                pass

            print("⚠️  Port-forward started but Prometheus not responding yet, continuing...")
            return True

        except Exception as e:
            print(f"❌ Failed to start port-forward: {e}")
            return False

    def stop_prometheus_port_forward(self):
        """Stop the kubectl port-forward process."""
        if self.port_forward_process and self.port_forward_process.poll() is None:
            print("🔌 Stopping Prometheus port-forward...")
            self.port_forward_process.terminate()
            try:
                self.port_forward_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.port_forward_process.kill()
            print("✅ Port-forward stopped")

    def deploy_manifest(self) -> bool:
        """
        Deploy the Kubernetes manifest using kubectl.

        Returns:
            True if deployment successful, False otherwise
        """
        print(f"\n{'='*60}")
        print(f"📦 Deploying manifest: {self.manifest_path}")
        print(f"{'='*60}")

        try:
            # Apply the manifest
            result = subprocess.run(
                ['kubectl', 'apply', '-f', str(self.manifest_path), '-n', self.namespace],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                print(f"❌ Failed to deploy manifest: {result.stderr}")
                return False

            print(f"✅ Manifest applied successfully")
            print(result.stdout)

            # Wait for deployments to be ready
            print("\n⏳ Waiting for pods to be ready...")
            time.sleep(5)  # Give K8s a moment to create resources

            # Get deployments from manifest and wait for them
            self._wait_for_deployments_ready()

            return True

        except subprocess.TimeoutExpired:
            print("❌ Deployment timed out")
            return False
        except Exception as e:
            print(f"❌ Error deploying manifest: {e}")
            return False

    def _wait_for_deployments_ready(self, timeout: int = 120):
        """
        Wait for all deployments in namespace to be ready.

        Args:
            timeout: Maximum time to wait in seconds
        """
        try:
            result = subprocess.run(
                ['kubectl', 'wait', '--for=condition=available',
                 '--timeout={}s'.format(timeout),
                 'deployment', '--all', '-n', self.namespace],
                capture_output=True,
                text=True,
                timeout=timeout + 10
            )

            if result.returncode == 0:
                print("✅ All deployments are ready")
            else:
                print(f"⚠️  Warning: Some deployments may not be ready: {result.stderr}")

        except Exception as e:
            print(f"⚠️  Warning: Could not verify deployment readiness: {e}")

    def run_locust_constant(
        self,
        users: int,
        think_time: float,
        duration: int,
        locustfile: str = "QNonK8s/locust/locustfile.py"
    ) -> bool:
        """
        Run Locust with constant load.

        Args:
            users: Number of concurrent users
            think_time: Think time between requests (seconds)
            duration: Test duration (seconds)
            locustfile: Path to locustfile

        Returns:
            True if Locust started successfully
        """
        print(f"\n{'='*60}")
        print(f"🚀 Starting Locust - Constant Load Mode")
        print(f"{'='*60}")
        print(f"  Users: {users}")
        print(f"  Think time: {think_time}s")
        print(f"  Duration: {duration}s")
        print(f"  Target: {self.locust_host}")

        # Set environment variables
        env = os.environ.copy()
        env['LOCUST_THINK_TIME'] = str(think_time)

        # Build Locust command
        cmd = [
            'locust',
            '-f', locustfile,
            '--headless',
            '--users', str(users),
            '--spawn-rate', str(min(users, 10)),  # Spawn max 10 users/sec
            '--run-time', f'{duration}s',
            '--host', self.locust_host,
            '--prometheus-port', '9646',
            '--csv', str(self.experiment_output_dir / 'locust')
        ]

        try:
            print(f"  Command: {' '.join(cmd)}")
            self.locust_process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            print("✅ Locust started successfully")

            # Start a thread to read Locust output for errors
            import threading
            def read_locust_output():
                if self.locust_process.stdout:
                    for line in self.locust_process.stdout:
                        line = line.strip()
                        if line and ('ERROR' in line or 'error' in line.lower() or 'Exception' in line or 'Traceback' in line):
                            print(f"  [Locust] {line}")

            output_thread = threading.Thread(target=read_locust_output, daemon=True)
            output_thread.start()

            return True

        except Exception as e:
            print(f"❌ Failed to start Locust: {e}")
            return False

    def run_locust_trace(
        self,
        trace_file: str,
        amplitude: int,
        shift: int,
        duration: int,
        think_time: float = 0.0,
        fit_trace: bool = False,
        locustfile: str = "QNonK8s/locust/locustfile_trace.py",
        entrypoint: str = "/"
    ) -> bool:
        """
        Run Locust with trace-based load shape.

        Args:
            trace_file: Path to workload CSV file
            amplitude: Maximum users above baseline
            shift: Minimum baseline users
            duration: Test duration (seconds)
            think_time: Think time between requests
            fit_trace: Whether to fit trace to duration
            locustfile: Path to locustfile
            entrypoint: API endpoint path

        Returns:
            True if Locust started successfully
        """
        print(f"\n{'='*60}")
        print(f"🚀 Starting Locust - Trace-Based Mode")
        print(f"{'='*60}")
        print(f"  Trace file: {trace_file}")
        print(f"  Amplitude: {amplitude}")
        print(f"  Shift: {shift}")
        print(f"  Duration: {duration}s")
        print(f"  Think time: {think_time}s")
        print(f"  Fit trace: {fit_trace}")
        print(f"  Target: {self.locust_host}")

        # Set environment variables
        env = os.environ.copy()
        env['LOCUST_TIME_LIMIT'] = str(duration)
        env['LOCUST_WORKLOAD_CSV'] = trace_file
        env['LOCUST_AMPLITUDE'] = str(amplitude)
        env['LOCUST_SHIFT'] = str(shift)
        env['LOCUST_THINK_TIME'] = str(think_time)
        env['LOCUST_FIT_TRACE'] = 'true' if fit_trace else 'false'
        env['LOCUST_ENTRYPOINT_PATH'] = entrypoint

        # Build Locust command
        cmd = [
            'locust',
            '-f', locustfile,
            '--headless',
            '--host', self.locust_host,
            '--prometheus-port', '9646',
            '--csv', str(self.experiment_output_dir / 'locust')
        ]

        try:
            self.locust_process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            print("✅ Locust started successfully")
            return True

        except Exception as e:
            print(f"❌ Failed to start Locust: {e}")
            return False

    def collect_metrics(self, duration: int, interval: int = 5) -> Dict[str, List]:
        """
        Collect metrics from Prometheus during the test.

        Args:
            duration: Collection duration (seconds)
            interval: Collection interval (seconds)

        Returns:
            Dictionary with time-series metrics data
        """
        print(f"\n{'='*60}")
        print(f"📊 Collecting metrics for {duration}s (interval: {interval}s)")
        print(f"{'='*60}")

        self.collector = PrometheusCollector(self.prometheus_url)

        # Check Prometheus health
        if not self.collector.health_check():
            print("❌ Prometheus is not accessible!")
            return {}

        print("✅ Prometheus is healthy")

        # Define metric types to collect (parametric templates + Locust metrics)
        metrics_to_collect = [
            'throughput',
            'response_time',
            'cpu_usage',
            'locust_users',
            'locust_requests_per_second',
            'locust_response_time_avg',
            'locust_failure_rate'
        ]

        # Initialize results dict (will be populated dynamically with parametric keys)
        results = {}
        start_time = time.time()
        next_collection = start_time

        try:
            while time.time() - start_time < duration:
                current_time = time.time()

                # Check if Locust is still running
                if self.locust_process and self.locust_process.poll() is not None:
                    print("⚠️  Locust has finished, stopping metrics collection")
                    break

                if current_time >= next_collection:
                    elapsed = current_time - start_time
                    timestamp = current_time

                    # Collect current metrics from Prometheus (server-side) using parametric queries
                    current_values = self.collector.get_current_metrics(
                        deployments=self.deployments_map,
                        metrics=metrics_to_collect
                    )

                    # Collect Locust metrics directly from exporter (client-side)
                    locust_direct = self.collector.get_locust_metrics_direct()

                    # Override Locust metrics with direct values
                    for key, value in locust_direct.items():
                        current_values[key] = value

                    # Get deployment replicas
                    replicas = self.collector.get_deployment_replicas(self.namespace)

                    # Add all metrics to results (create new keys as needed for parametric queries)
                    # Filter out None and NaN values (Prometheus warm-up period)
                    for metric_key, value in current_values.items():
                        # Skip None, NaN, and infinite values
                        if value is not None and not (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
                            if metric_key not in results:
                                results[metric_key] = []
                            results[metric_key].append({
                                'timestamp': timestamp,
                                'elapsed': elapsed,
                                'value': value
                            })

                    # Add replica counts
                    if 'replicas' not in results:
                        results['replicas'] = []
                    results['replicas'].append({
                        'timestamp': timestamp,
                        'elapsed': elapsed,
                        'data': replicas
                    })

                    # Safe formatting with None handling - show Locust client-side metrics
                    users = current_values.get('locust_users') or 0
                    rps = current_values.get('locust_requests_per_second') or 0
                    rt_client = current_values.get('locust_response_time_avg') or 0  # Client-side RT from Locust

                    print(f"  ✓ Collected at t={elapsed:.1f}s | "
                          f"Users: {users:.0f} | "
                          f"RPS: {rps:.2f} | "
                          f"Client RT: {rt_client:.1f}ms")

                    next_collection += interval

                time.sleep(0.5)  # Small sleep to avoid busy waiting

        except KeyboardInterrupt:
            print("\n⚠️  Metrics collection interrupted by user")

        finally:
            if self.collector:
                self.collector.close()

        print(f"✅ Metrics collection completed")
        return results

    def wait_for_locust(self, duration: int):
        """
        Wait for Locust to complete and show output.

        Args:
            duration: Expected test duration (seconds)
        """
        if not self.locust_process:
            return

        print(f"\n{'='*60}")
        print(f"⏳ Monitoring Locust execution...")
        print(f"{'='*60}")

        try:
            # Monitor Locust output
            start_time = time.time()
            while self.locust_process.poll() is None:
                if time.time() - start_time > duration + 30:
                    print("⚠️  Locust taking longer than expected...")
                    break
                time.sleep(1)

            # Get any remaining output
            if self.locust_process.stdout:
                output = self.locust_process.stdout.read()
                if output:
                    print("\n--- Locust Output ---")
                    print(output)

            returncode = self.locust_process.returncode
            if returncode == 0:
                print("✅ Locust completed successfully")
            else:
                print(f"⚠️  Locust exited with code {returncode}")

        except KeyboardInterrupt:
            print("\n⚠️  Interrupted - stopping Locust...")
            self.stop_locust()

    def stop_locust(self):
        """Stop the Locust process."""
        if self.locust_process and self.locust_process.poll() is None:
            print("🛑 Stopping Locust...")
            self.locust_process.send_signal(signal.SIGINT)
            try:
                self.locust_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.locust_process.kill()
            print("✅ Locust stopped")

    def cleanup_deployment(self):
        """Delete the deployed Kubernetes resources."""
        if not self.cleanup:
            print("\n⏭️  Skipping cleanup (--no-cleanup specified)")
            return

        print(f"\n{'='*60}")
        print(f"🧹 Cleaning up deployment")
        print(f"{'='*60}")

        try:
            result = subprocess.run(
                ['kubectl', 'delete', '-f', str(self.manifest_path), '-n', self.namespace],
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                print("✅ Deployment cleaned up successfully")
            else:
                print(f"⚠️  Warning during cleanup: {result.stderr}")

        except Exception as e:
            print(f"⚠️  Error during cleanup: {e}")

    def save_summary_statistics(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Save summary statistics (average values) to CSV - main output comparable with JMT.

        Args:
            df: DataFrame with time-series metrics

        Returns:
            DataFrame with summary statistics
        """
        print(f"\n{'='*60}")
        print(f"📊 Computing Summary Statistics (comparable with JMT)")
        print(f"{'='*60}")

        summary_data = {
            'Metric': [],
            'Mean': [],
            'Std Dev': [],
            'Min': [],
            'Max': [],
            'Unit': []
        }

        # Start with static Locust metrics (client-side)
        metrics_config = [
            ('locust_users', 'Users', 'users'),
            ('locust_requests_per_second', 'Client RPS', 'req/s'),
            ('locust_response_time_avg', 'Client Response Time', 'ms'),
            ('locust_failure_rate', 'Client Failure Rate', '%'),
        ]

        # Add system-wide metrics if present
        if 'throughput' in df.columns:
            metrics_config.append(('throughput', 'System Throughput', 'req/s'))
        if 'response_time_avg' in df.columns:
            metrics_config.append(('response_time_avg', 'System Response Time', 'ms'))

        # Dynamically discover per-task metrics from deployments_map
        # With parametric queries, columns are named:
        #   - throughput_{deployment_key} (e.g., throughput_task1-deployment)
        #   - response_time_{service_name} (e.g., response_time_task1-svc)
        #   - cpu_usage_{task_name} (e.g., cpu_usage_task1)
        #   - {task_name}_replicas (e.g., task1_replicas)

        for deployment_key, deployment_info in self.deployments_map.items():
            task_name = deployment_info['task']
            service_name = deployment_info['service']
            jmt_station = deployment_info.get('jmt_station', task_name)

            # Throughput: check for throughput_{deployment_key}
            throughput_col = f'throughput_{deployment_key}'
            if throughput_col in df.columns:
                metrics_config.append((throughput_col, f'{jmt_station} Throughput', 'req/s'))

            # Response time: check for response_time_{service_name}
            response_time_col = f'response_time_{service_name}'
            if response_time_col in df.columns:
                metrics_config.append((response_time_col, f'{jmt_station} Response Time', 'ms'))

            # CPU usage: check for cpu_usage_{task_name}
            cpu_col = f'cpu_usage_{task_name}'
            if cpu_col in df.columns:
                metrics_config.append((cpu_col, f'{jmt_station} CPU Utilization', 'cores'))

            # Replicas: check for {task_name}_replicas
            replicas_col = f'{task_name}_replicas'
            if replicas_col in df.columns:
                metrics_config.append((replicas_col, f'{jmt_station} Replicas', 'pods'))

        # Extract statistics for all discovered metrics
        for col_name, display_name, unit in metrics_config:
            if col_name in df.columns and not df[col_name].isna().all():
                summary_data['Metric'].append(display_name)
                summary_data['Mean'].append(df[col_name].mean())
                summary_data['Std Dev'].append(df[col_name].std())
                summary_data['Min'].append(df[col_name].min())
                summary_data['Max'].append(df[col_name].max())
                summary_data['Unit'].append(unit)

        summary_df = pd.DataFrame(summary_data)

        # Save summary to CSV
        summary_path = self.experiment_output_dir / 'summary_statistics.csv'
        summary_df.to_csv(summary_path, index=False, float_format='%.4f')
        print(f"✅ Summary statistics saved to: {summary_path}")

        # Print summary table to console
        print(f"\n📋 Experiment Summary (Mean Values):")
        print("=" * 80)
        for _, row in summary_df.iterrows():
            print(f"  {row['Metric']:.<40} {row['Mean']:>10.4f} {row['Unit']}")
        print("=" * 80)

        return summary_df

    def save_results_to_csv(self, metrics_data: Dict[str, List]):
        """
        Save collected metrics to detailed time-series CSV file.

        Args:
            metrics_data: Dictionary with metrics time-series data

        Returns:
            DataFrame with consolidated metrics
        """
        print(f"\n{'='*60}")
        print(f"💾 Saving detailed time-series data")
        print(f"{'='*60}")

        # Convert metrics data to DataFrame with wide format
        rows = []

        # Find the metric with data to get timestamps
        reference_metric = None
        for metric_name, data_points in metrics_data.items():
            if metric_name != 'replicas' and isinstance(data_points, list) and len(data_points) > 0:
                reference_metric = metric_name
                break

        if not reference_metric:
            print("⚠️  No metrics data to save")
            return pd.DataFrame()

        num_samples = len(metrics_data[reference_metric])

        for i in range(num_samples):
            row = {
                'timestamp': metrics_data[reference_metric][i]['timestamp'],
                'elapsed': metrics_data[reference_metric][i]['elapsed'],
            }

            # Add all metrics
            for metric_name, data_points in metrics_data.items():
                if metric_name == 'replicas':
                    # Add replica counts inline
                    if i < len(data_points):
                        replica_data = data_points[i]['data']
                        for deployment, counts in replica_data.items():
                            # Clean deployment name (remove -deployment suffix)
                            clean_name = deployment.replace('-deployment', '')
                            row[f'{clean_name}_replicas'] = counts['ready']
                else:
                    if i < len(data_points):
                        row[metric_name] = data_points[i]['value']
                    else:
                        row[metric_name] = None

            rows.append(row)

        df = pd.DataFrame(rows)

        # Reorder columns for better readability
        desired_order = [
            'timestamp', 'elapsed',
            # Client-side metrics (Locust)
            'locust_users', 'locust_requests_per_second', 'locust_response_time_avg', 'locust_failure_rate',
            # Server-side metrics (Istio) - aggregated
            'throughput', 'response_time_avg',
            # Per-task metrics
            'throughput_task1', 'response_time_task1', 'cpu_usage_task1',
            'throughput_task2', 'response_time_task2', 'cpu_usage_task2',
            # Replicas
            'task1_replicas', 'task2_replicas',
        ]

        # Only include columns that exist
        final_columns = [col for col in desired_order if col in df.columns]
        # Add any remaining columns not in desired order
        remaining_columns = [col for col in df.columns if col not in final_columns]
        final_columns.extend(remaining_columns)

        df = df[final_columns]

        # Save detailed time-series to CSV
        csv_path = self.experiment_output_dir / 'metrics_timeseries.csv'
        df.to_csv(csv_path, index=False)
        print(f"✅ Detailed time-series saved to: {csv_path}")

        return df

    def generate_plots(self, df: pd.DataFrame):
        """
        Generate descriptive plots from metrics data.

        Args:
            df: DataFrame with metrics data
        """
        print(f"\n{'='*60}")
        print(f"📈 Generating plots")
        print(f"{'='*60}")

        fig, axes = plt.subplots(2, 2, figsize=(16, 12))
        fig.suptitle(f'Performance Metrics - Experiment {self.experiment_id}',
                     fontsize=16, fontweight='bold')

        # Plot 1: Throughput over time
        ax1 = axes[0, 0]
        if 'throughput' in df.columns:
            ax1.plot(df['elapsed'], df['throughput'], 'b-', linewidth=2, label='Server Throughput', alpha=0.6)
            mean_val = df['throughput'].mean()
            ax1.axhline(y=mean_val, color='b', linestyle='--', linewidth=2,
                       label=f'Mean: {mean_val:.2f} req/s')
        if 'locust_requests_per_second' in df.columns:
            ax1.plot(df['elapsed'], df['locust_requests_per_second'], 'r-', linewidth=2,
                    label='Client RPS', alpha=0.6)
            mean_val = df['locust_requests_per_second'].mean()
            ax1.axhline(y=mean_val, color='r', linestyle='--', linewidth=2,
                       label=f'Mean: {mean_val:.2f} req/s')
        ax1.set_xlabel('Time (seconds)')
        ax1.set_ylabel('Requests/Second')
        ax1.set_title('Throughput Over Time (with Mean)')
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # Plot 2: Response time over time
        ax2 = axes[0, 1]
        if 'response_time_avg' in df.columns:
            ax2.plot(df['elapsed'], df['response_time_avg'], 'g-', linewidth=2,
                    label='Server Response Time', alpha=0.6)
            mean_val = df['response_time_avg'].mean()
            ax2.axhline(y=mean_val, color='g', linestyle='--', linewidth=2,
                       label=f'Mean: {mean_val:.2f} ms')
        if 'locust_response_time_avg' in df.columns:
            ax2.plot(df['elapsed'], df['locust_response_time_avg'], 'm-', linewidth=2,
                    label='Client Response Time', alpha=0.6)
            mean_val = df['locust_response_time_avg'].mean()
            ax2.axhline(y=mean_val, color='m', linestyle='--', linewidth=2,
                       label=f'Mean: {mean_val:.2f} ms')
        ax2.set_xlabel('Time (seconds)')
        ax2.set_ylabel('Response Time (ms)')
        ax2.set_title('Response Time Over Time (with Mean)')
        ax2.legend()
        ax2.grid(True, alpha=0.3)

        # Plot 3: CPU usage over time
        ax3 = axes[1, 0]
        if 'cpu_usage_task1' in df.columns:
            ax3.plot(df['elapsed'], df['cpu_usage_task1'], 'c-', linewidth=2,
                    label='Task1 CPU', alpha=0.6)
            mean_val = df['cpu_usage_task1'].mean()
            ax3.axhline(y=mean_val, color='c', linestyle='--', linewidth=2,
                       label=f'Task1 Mean: {mean_val:.4f} cores')
        if 'cpu_usage_task2' in df.columns:
            ax3.plot(df['elapsed'], df['cpu_usage_task2'], 'y-', linewidth=2,
                    label='Task2 CPU', alpha=0.6)
            mean_val = df['cpu_usage_task2'].mean()
            ax3.axhline(y=mean_val, color='y', linestyle='--', linewidth=2,
                       label=f'Task2 Mean: {mean_val:.4f} cores')
        ax3.set_xlabel('Time (seconds)')
        ax3.set_ylabel('CPU Cores')
        ax3.set_title('CPU Utilization Over Time (with Mean)')
        ax3.legend()
        ax3.grid(True, alpha=0.3)

        # Plot 4: Active users over time
        ax4 = axes[1, 1]
        if 'locust_users' in df.columns:
            ax4.plot(df['elapsed'], df['locust_users'], 'purple', linewidth=2, alpha=0.6)
            ax4.fill_between(df['elapsed'], df['locust_users'], alpha=0.2, color='purple')
            mean_val = df['locust_users'].mean()
            ax4.axhline(y=mean_val, color='purple', linestyle='--', linewidth=2,
                       label=f'Mean: {mean_val:.1f} users')
        ax4.set_xlabel('Time (seconds)')
        ax4.set_ylabel('Active Users')
        ax4.set_title('Active Users Over Time (with Mean)')
        ax4.legend()
        ax4.grid(True, alpha=0.3)

        plt.tight_layout()

        # Save plot
        plot_path = self.experiment_output_dir / 'metrics_plots.png'
        plt.savefig(plot_path, dpi=150, bbox_inches='tight')
        print(f"✅ Plots saved to: {plot_path}")
        plt.close()


def main():
    """Main entry point for the experiment runner."""
    parser = argparse.ArgumentParser(
        description='Kubernetes Performance Testing Experiment Runner',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    # Required arguments
    parser.add_argument('--manifest', required=True, help='Path to Kubernetes manifest YAML')
    parser.add_argument('--mode', required=True, choices=['constant', 'trace'],
                        help='Load testing mode')

    # Common arguments
    parser.add_argument('--duration', type=int, required=True,
                        help='Test duration in seconds')
    parser.add_argument('--namespace', default='default',
                        help='Kubernetes namespace (default: default)')
    parser.add_argument('--prometheus-url', default='http://localhost:9090',
                        help='Prometheus server URL')
    parser.add_argument('--locust-host', default=None,
                        help='Target host for Locust (auto-detected if not specified)')
    parser.add_argument('--no-auto-detect', action='store_true',
                        help='Disable automatic Ingress Gateway detection')
    parser.add_argument('--skip-port-forward', action='store_true',
                        help='Skip automatic Prometheus port-forward setup')
    parser.add_argument('--output-dir', default='results',
                        help='Output directory for results')
    parser.add_argument('--no-cleanup', action='store_true',
                        help='Do not cleanup deployments after test')
    parser.add_argument('--metrics-interval', type=int, default=5,
                        help='Metrics collection interval in seconds (default: 5)')
    parser.add_argument('--config', required=True,
                        help='Path to experiment_mapping.yaml (required for parametric queries and JMT validation)')
    parser.add_argument('--gateway-manifest', default=None,
                        help='Path to Istio Gateway/VirtualService manifest (auto-applied if Gateway not found)')
    parser.add_argument('--skip-jmt-validation', action='store_true',
                        help='Skip JMT model validation (validation runs by default if JMT is available)')

    # Constant mode arguments
    parser.add_argument('--users', type=int,
                        help='Number of concurrent users (constant mode)')
    parser.add_argument('--think-time', type=float,
                        help='Think time between requests in seconds (constant mode)')

    # Trace mode arguments
    parser.add_argument('--trace-file', help='Path to workload CSV file (trace mode)')
    parser.add_argument('--amplitude', type=int, default=50,
                        help='Maximum users above baseline (trace mode, default: 50)')
    parser.add_argument('--shift', type=int, default=10,
                        help='Minimum baseline users (trace mode, default: 10)')
    parser.add_argument('--trace-think-time', type=float, default=0.0,
                        help='Think time for trace mode (default: 0.0)')
    parser.add_argument('--fit-trace', action='store_true',
                        help='Fit trace to duration (trace mode)')

    args = parser.parse_args()

    # Validate mode-specific arguments
    if args.mode == 'constant':
        if not args.users or args.think_time is None:
            parser.error("Constant mode requires --users and --think-time")
    elif args.mode == 'trace':
        if not args.trace_file:
            parser.error("Trace mode requires --trace-file")

    # Create experiment runner
    runner = ExperimentRunner(
        manifest_path=args.manifest,
        namespace=args.namespace,
        prometheus_url=args.prometheus_url,
        output_dir=args.output_dir,
        locust_host=args.locust_host,
        cleanup=not args.no_cleanup,
        auto_detect_ingress=not args.no_auto_detect,
        auto_port_forward=not args.skip_port_forward,
        config_path=args.config,
        gateway_manifest_path=args.gateway_manifest
    )

    print(f"\n{'='*60}")
    print(f"🧪 Kubernetes Performance Experiment Runner")
    print(f"{'='*60}")
    print(f"Experiment ID: {runner.experiment_id}")
    print(f"Mode: {args.mode}")
    print(f"Duration: {args.duration}s")
    print(f"Output directory: {args.output_dir}")

    try:
        # Step 0: Setup Prometheus port-forward
        if not runner.start_prometheus_port_forward():
            print("\n❌ Failed to setup Prometheus port-forward - aborting")
            sys.exit(1)

        # Step 1: Deploy manifest
        if not runner.deploy_manifest():
            print("\n❌ Deployment failed - aborting")
            sys.exit(1)

        # Step 2: Start Locust
        if args.mode == 'constant':
            success = runner.run_locust_constant(
                users=args.users,
                think_time=args.think_time,
                duration=args.duration
            )
        else:  # trace mode
            success = runner.run_locust_trace(
                trace_file=args.trace_file,
                amplitude=args.amplitude,
                shift=args.shift,
                duration=args.duration,
                think_time=args.trace_think_time,
                fit_trace=args.fit_trace
            )

        if not success:
            print("\n❌ Failed to start Locust - aborting")
            runner.cleanup_deployment()
            sys.exit(1)

        # Give Locust a moment to start
        time.sleep(5)

        # Step 3: Collect metrics (runs in parallel with Locust)
        metrics_data = runner.collect_metrics(
            duration=args.duration,
            interval=args.metrics_interval
        )

        # Step 4: Wait for Locust to finish
        runner.wait_for_locust(args.duration)

        # Step 5: Save results
        if metrics_data:
            df = runner.save_results_to_csv(metrics_data)

            # Save summary statistics (main output comparable with JMT)
            summary_df = runner.save_summary_statistics(df)

            # Generate plots
            runner.generate_plots(df)

            # Step 5b: JMT Validation (runs by default unless --skip-jmt-validation is set)
            if not args.skip_jmt_validation:
                if not JMT_AVAILABLE:
                    print("\n⚠️  JMT validator not available (import failed)")
                else:
                    print(f"\n{'='*60}")
                    print(f"🔬 Running JMT Model Validation")
                    print(f"{'='*60}")

                    try:
                        with open(args.config, 'r') as f:
                            mapping_config = yaml.safe_load(f)

                        # Initialize validator
                        jmt_jar = mapping_config['jmt']['jar_path']
                        validator = JMTValidator(jmt_jar_path=jmt_jar)

                        # Extract K8s parameters
                        k8s_params = validator.extract_k8s_parameters(args.manifest)
                        print(f"📋 Extracted K8s parameters:")
                        for svc, params in k8s_params.items():
                            print(f"  {svc}: service_time={params['service_time']}s, replicas={params['replicas']}")

                        # Update mapping config with actual experiment parameters
                        network_type = mapping_config['jmt'].get('network_type', 'open')

                        if network_type == 'closed':
                            # For closed networks, update customers and think_time
                            if 'closed_network' not in mapping_config['jmt']:
                                mapping_config['jmt']['closed_network'] = {}

                            # Use Locust users as the number of customers in the closed network
                            mapping_config['jmt']['closed_network']['customers'] = args.users

                            # Update think_time from experiment parameters
                            mapping_config['workload']['think_time'] = args.think_time

                            print(f"📈 Closed network: customers={args.users}, think_time={args.think_time}s")

                        else:
                            # For open networks, calculate arrival rate
                            if args.mode == 'constant':
                                # arrival_rate ≈ users / (think_time + avg_response_time)
                                avg_rt = df['locust_response_time_avg'].mean() / 1000.0 if 'locust_response_time_avg' in df.columns else 0.2
                                arrival_rate = args.users / (args.think_time + avg_rt)
                            else:
                                # Use from config for trace mode
                                arrival_rate = mapping_config['workload'].get('arrival_rate', 1.0)

                            mapping_config['workload']['arrival_rate'] = arrival_rate
                            print(f"📈 Open network: arrival_rate={arrival_rate:.2f} req/s")

                        # Detect template type and use appropriate method
                        template_path = mapping_config['jmt']['template']
                        solver_type = mapping_config['jmt'].get('solver_type', 'sim')

                        # Auto-detect from extension if not specified
                        if template_path.endswith('.jmva'):
                            solver_type = 'mva'
                        elif template_path.endswith('.jsimg'):
                            solver_type = 'sim'

                        print(f"🔧 Modifying JMT template: {template_path} (solver: {solver_type})")

                        if solver_type == 'mva':
                            # Use JMVA analytical solver
                            modified_template = validator.modify_jmva_template(
                                template_path=template_path,
                                mapping_config=mapping_config,
                                k8s_params=k8s_params
                            )
                            print(f"✅ Modified JMVA template saved")

                            # Run JMVA analytical solver
                            jmt_results = validator.run_jmva_solution(modified_template)

                        else:
                            # Use JSIMgraph simulation
                            modified_template = validator.modify_jmt_template(
                                template_path=template_path,
                                mapping_config=mapping_config,
                                k8s_params=k8s_params
                            )
                            print(f"✅ Modified simulation template saved")

                            # Run JMT simulation
                            max_sim_time = mapping_config['jmt'].get('max_simulation_time', 120)
                            jmt_results = validator.run_jmt_simulation(modified_template, max_time=max_sim_time)

                        if jmt_results:
                            print(f"📊 JMT Simulation Results:")
                            for station, metrics in jmt_results.items():
                                print(f"  {station}: {metrics}")

                            # Validate against measurements
                            validation = validator.validate_against_measurements(
                                jmt_results, df, mapping_config
                            )

                            # Generate validation report
                            report_path = runner.experiment_output_dir / 'jmt_validation_report.txt'
                            validator.generate_validation_report(validation, str(report_path))

                            # Save JMT results to CSV for what-if analysis
                            jmt_csv_path = runner.experiment_output_dir / 'jmt_validation_results.csv'
                            jmt_csv_data = []
                            for task_name, station_data in validation['per_station'].items():
                                for metric in ['throughput', 'response_time', 'utilization']:
                                    if metric in station_data.get('theoretical', {}):
                                        jmt_csv_data.append({
                                            'station': station_data.get('jmt_station', ''),
                                            'task': task_name,
                                            'metric': metric,
                                            'jmt_predicted': station_data['theoretical'].get(metric, 0),
                                            'measured': station_data['measured'].get(metric, 0),
                                            'error_pct': station_data['errors'].get(f'{metric}_error_pct', 0)
                                        })
                            if jmt_csv_data:
                                jmt_csv_df = pd.DataFrame(jmt_csv_data)
                                jmt_csv_df.to_csv(jmt_csv_path, index=False)
                                print(f"📊 JMT results CSV saved to: {jmt_csv_path}")
                        else:
                            print("⚠️  JMT simulation returned no results")

                    except Exception as e:
                        print(f"❌ JMT validation failed: {e}")
                        import traceback
                        traceback.print_exc()
        else:
            print("⚠️  No metrics collected")

        # Step 6: Cleanup
        runner.cleanup_deployment()
        runner.stop_prometheus_port_forward()

        print(f"\n{'='*60}")
        print(f"✅ Experiment completed successfully!")
        print(f"{'='*60}")
        print(f"Results saved in: {runner.experiment_output_dir}")

    except KeyboardInterrupt:
        print("\n\n⚠️  Experiment interrupted by user")
        runner.stop_locust()
        runner.cleanup_deployment()
        runner.stop_prometheus_port_forward()
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Experiment failed with error: {e}")
        import traceback
        traceback.print_exc()
        runner.stop_locust()
        runner.cleanup_deployment()
        runner.stop_prometheus_port_forward()
        sys.exit(1)


if __name__ == '__main__':
    main()
