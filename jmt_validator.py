#!/usr/bin/env python3
"""
JMT Validator Module

Validates measured performance metrics against theoretical predictions
from JMT (Java Modelling Tools) queueing network models.
"""

import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Optional, Tuple
import yaml
import re
import pandas as pd


class JMTValidator:
    """
    Validates experimental results against JMT queueing network models.
    """

    def __init__(self, jmt_jar_path: str = "/Users/emilio-imt/JMT-singlejar-1.2.2.jar"):
        """
        Initialize the JMT validator.

        Args:
            jmt_jar_path: Path to JMT JAR file
        """
        self.jmt_jar_path = Path(jmt_jar_path)
        if not self.jmt_jar_path.exists():
            raise FileNotFoundError(f"JMT JAR not found at {jmt_jar_path}")

    def load_mapping_config(self, config_path: str) -> Dict:
        """
        Load the mapping configuration between K8s manifest and JMT template.

        Args:
            config_path: Path to YAML configuration file

        Returns:
            Configuration dictionary
        """
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)

    def extract_k8s_parameters(self, manifest_path: str) -> Dict:
        """
        Extract service parameters from Kubernetes manifest.

        Args:
            manifest_path: Path to K8s manifest YAML

        Returns:
            Dictionary with service parameters
        """
        with open(manifest_path, 'r') as f:
            # Load all documents (manifest may have multiple resources)
            docs = list(yaml.safe_load_all(f))

        services = {}

        for doc in docs:
            if not doc:
                continue

            kind = doc.get('kind', '')

            if kind == 'Deployment':
                name = doc['metadata']['name']
                spec = doc['spec']['template']['spec']

                # Extract container environment variables
                containers = spec.get('containers', [])
                if containers:
                    container = containers[0]
                    env_vars = {}
                    for env in container.get('env', []):
                        env_vars[env['name']] = env['value']

                    services[name] = {
                        'replicas': doc['spec'].get('replicas', 1),
                        'service_time': float(env_vars.get('SERVICE_TIME_SECONDS', 0.1)),
                        'cpu_limit': self._parse_cpu_resource(
                            container.get('resources', {}).get('limits', {}).get('cpu', '1')
                        ),
                        'env_vars': env_vars
                    }

        return services

    def _parse_cpu_resource(self, cpu_str: str) -> float:
        """
        Parse Kubernetes CPU resource string to float.

        Args:
            cpu_str: CPU string like '1000m' or '1'

        Returns:
            CPU cores as float
        """
        if isinstance(cpu_str, (int, float)):
            return float(cpu_str)

        cpu_str = str(cpu_str)
        if cpu_str.endswith('m'):
            return float(cpu_str[:-1]) / 1000.0
        else:
            return float(cpu_str)

    def modify_jmt_template(
        self,
        template_path: str,
        mapping_config: Dict,
        k8s_params: Dict[str, Dict],
        output_path: Optional[str] = None
    ) -> str:
        """
        Modify JMT template with actual parameters using generic mapping config.

        Args:
            template_path: Path to JMT template file
            mapping_config: Mapping configuration dictionary
            k8s_params: K8s parameters extracted from manifest
            output_path: Output path for modified template (optional)

        Returns:
            Path to the modified template file
        """
        tree = ET.parse(template_path)
        root = tree.getroot()

        # Find the sim element
        sim = root.find('sim')
        if sim is None:
            raise ValueError("Invalid JMT template: missing <sim> element")

        # Get configuration
        jmt_config = mapping_config.get('jmt', {})
        network_type = jmt_config.get('network_type', 'open')
        workload = mapping_config.get('workload', {})
        station_mapping = mapping_config.get('mapping', {})

        # Update userClass for closed networks
        if network_type == 'closed':
            closed_config = jmt_config.get('closed_network', {})
            customers = closed_config.get('customers', 1)

            # Find and update userClass element
            for user_class in sim.findall('userClass'):
                user_class.set('customers', str(customers))
                print(f"  - Updated userClass customers: {customers}")

        # Iterate through mapping and update each station
        for deployment_name, station_config in station_mapping.items():
            jmt_station_name = station_config.get('jmt_station')
            station_type = station_config.get('station_type')
            update_params = station_config.get('update_params', [])

            if not jmt_station_name or not station_type:
                continue

            # Find the node with this exact name
            node = None
            for n in sim.findall('node'):
                if n.get('name') == jmt_station_name:
                    node = n
                    break

            if node is None:
                print(f"  ⚠️  Station '{jmt_station_name}' not found in template")
                continue

            print(f"  - Updating station: {jmt_station_name} (type: {station_type})")

            # Update based on station type and parameters
            if station_type == 'Server':
                self._update_server_station(
                    node, deployment_name, k8s_params.get(deployment_name, {}), update_params
                )
            elif station_type == 'Delay':
                self._update_delay_station(
                    node, workload.get('think_time', 1.0), update_params
                )
            elif station_type == 'RandomSource':
                self._update_source_station(
                    node, workload.get('arrival_rate', 1.0), update_params
                )

        # Save modified template
        if output_path is None:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.jsimg', delete=False) as f:
                output_path = f.name

        tree.write(output_path, encoding='ISO-8859-1', xml_declaration=True)
        print(f"✅ Modified template saved to: {output_path}")
        return output_path

    def _update_server_station(
        self,
        node: ET.Element,
        deployment_name: str,
        k8s_params: Dict,
        update_params: list
    ):
        """
        Update Server station parameters.

        Args:
            node: XML node element for the station
            deployment_name: Name of the K8s deployment
            k8s_params: Parameters from K8s manifest
            update_params: List of parameters to update
        """
        # Find Server section
        server_section = None
        for section in node.findall('section'):
            if section.get('className') == 'Server':
                server_section = section
                break

        if server_section is None:
            print(f"    ⚠️  No Server section found")
            return

        # Update service time lambda
        if 'service_time_lambda' in update_params:
            service_time = k8s_params.get('service_time', 0.1)
            service_rate = 1.0 / service_time  # lambda = 1/service_time

            # Find and update lambda in ServiceStrategy
            for param in server_section.findall('.//subParameter[@name="lambda"]/value'):
                param.text = str(service_rate)
                print(f"    - service_time: {service_time}s → lambda: {service_rate:.2f}")

        # Update number of servers (replicas)
        if 'num_servers' in update_params:
            replicas = k8s_params.get('replicas', 1)

            # Update maxJobs
            for param in server_section.findall('parameter[@name="maxJobs"]/value'):
                param.text = str(replicas)

            # Update serversPerServerType
            for param in server_section.findall('.//subParameter[@name="serverTypesNumOfServers"]/value'):
                param.text = str(replicas)

            print(f"    - num_servers: {replicas}")

    def _update_delay_station(
        self,
        node: ET.Element,
        think_time: float,
        update_params: list
    ):
        """
        Update Delay station (think time).

        Args:
            node: XML node element for the station
            think_time: Think time in seconds
            update_params: List of parameters to update
        """
        # Find Delay section
        delay_section = None
        for section in node.findall('section'):
            if section.get('className') == 'Delay':
                delay_section = section
                break

        if delay_section is None:
            print(f"    ⚠️  No Delay section found")
            return

        # Update think time lambda
        if 'think_time_lambda' in update_params:
            think_time_rate = 1.0 / think_time  # lambda = 1/think_time

            # Find and update lambda in ServiceStrategy
            for param in delay_section.findall('.//subParameter[@name="lambda"]/value'):
                param.text = str(think_time_rate)
                print(f"    - think_time: {think_time}s → lambda: {think_time_rate:.2f}")

    def _update_source_station(
        self,
        node: ET.Element,
        arrival_rate: float,
        update_params: list
    ):
        """
        Update RandomSource station (for open networks).

        Args:
            node: XML node element for the station
            arrival_rate: Arrival rate (lambda)
            update_params: List of parameters to update
        """
        # Find RandomSource section
        source_section = None
        for section in node.findall('section'):
            if section.get('className') == 'RandomSource':
                source_section = section
                break

        if source_section is None:
            print(f"    ⚠️  No RandomSource section found")
            return

        # Update arrival rate lambda
        if 'arrival_rate_lambda' in update_params:
            # Find and update lambda in ServiceStrategy
            for param in source_section.findall('.//subParameter[@name="lambda"]/value'):
                param.text = str(arrival_rate)
                print(f"    - arrival_rate: {arrival_rate} req/s")

    def modify_jmva_template(
        self,
        template_path: str,
        mapping_config: Dict,
        k8s_params: Dict[str, Dict],
        output_path: Optional[str] = None
    ) -> str:
        """
        Modify JMVA (analytical MVA) template with actual parameters.

        Args:
            template_path: Path to JMVA template file (.jmva)
            mapping_config: Mapping configuration dictionary
            k8s_params: K8s parameters extracted from manifest
            output_path: Output path for modified template (optional)

        Returns:
            Path to the modified template file
        """
        tree = ET.parse(template_path)
        root = tree.getroot()

        # Find the parameters element
        params = root.find('parameters')
        if params is None:
            raise ValueError("Invalid JMVA template: missing <parameters> element")

        # Get configuration
        jmt_config = mapping_config.get('jmt', {})
        workload = mapping_config.get('workload', {})
        station_mapping = mapping_config.get('mapping', {})

        # Update closed class population (number of customers)
        closed_config = jmt_config.get('closed_network', {})
        customers = closed_config.get('customers', 1)

        classes_elem = params.find('classes')
        if classes_elem is not None:
            for closed_class in classes_elem.findall('closedclass'):
                closed_class.set('population', str(customers))
                print(f"  - Updated population: {customers} customers")

        # Update stations
        stations_elem = params.find('stations')
        if stations_elem is None:
            raise ValueError("Invalid JMVA template: missing <stations> element")

        # Iterate through mapping and update each station
        for deployment_name, station_config in station_mapping.items():
            jmt_station_name = station_config.get('jmt_station')
            station_type = station_config.get('station_type')

            if not jmt_station_name:
                continue

            print(f"  - Updating station: {jmt_station_name} (type: {station_type})")

            # Find station by name
            station = None
            if station_type == 'Delay':
                station = stations_elem.find(f".//delaystation[@name='{jmt_station_name}']")
            elif station_type == 'Server':
                station = stations_elem.find(f".//listation[@name='{jmt_station_name}']")

            if station is None:
                print(f"    ⚠️  Station '{jmt_station_name}' not found in template")
                continue

            # Update based on station type
            if station_type == 'Delay':
                # Update think time
                think_time = workload.get('think_time', 1.0)
                servicetimes = station.find('servicetimes')
                if servicetimes is not None:
                    for st in servicetimes.findall('servicetime'):
                        st.text = str(think_time)
                        print(f"    - think_time: {think_time}s")

            elif station_type == 'Server':
                # Update service time
                k8s_station_params = k8s_params.get(deployment_name, {})
                service_time = k8s_station_params.get('service_time', 0.1)

                servicetimes = station.find('servicetimes')
                if servicetimes is not None:
                    for st in servicetimes.findall('servicetime'):
                        st.text = str(service_time)
                        print(f"    - service_time: {service_time}s")

                # Update number of servers (replicas)
                replicas = k8s_station_params.get('replicas', 1)
                station.set('servers', str(replicas))
                print(f"    - servers: {replicas}")

        # Update algorithm parameters
        alg_params = root.find('algParams')
        if alg_params is not None:
            alg_type = alg_params.find('algType')
            if alg_type is not None and alg_type.get('name') != 'MVA':
                alg_type.set('name', 'MVA')

        # Save modified template
        if output_path is None:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.jmva', delete=False) as f:
                output_path = f.name

        tree.write(output_path, encoding='UTF-8', xml_declaration=True)
        print(f"✅ Modified JMVA template saved to: {output_path}")
        return output_path

    def run_jmva_solution(self, model_path: str) -> Dict:
        """
        Run JMVA (analytical MVA solver) and extract results.

        Args:
            model_path: Path to JMVA model file

        Returns:
            Dictionary with analytical results
        """
        print(f"🔬 Running JMVA analytical solver...")

        try:
            # Run JMVA using command line interface
            # Command: java -cp JMT.JAR jmt.commandline.Jmt mva <File.jmva>
            cmd = [
                'java', '-cp', str(self.jmt_jar_path),
                'jmt.commandline.Jmt', 'mva', model_path
            ]

            print(f"  Command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                print(f"⚠️  JMVA warning: {result.stderr}")
                if result.stdout:
                    print(f"  Output: {result.stdout}")

            # Parse results from output file
            # JMVA creates a file named <original>-result.jmva
            model_dir = Path(model_path).parent
            model_name = Path(model_path).stem
            result_file = model_dir / f"{model_name}-result.jmva"

            if not result_file.exists():
                print(f"❌ Results file not found: {result_file}")
                return {}

            print(f"✅ JMVA solution completed")
            results = self._parse_jmva_results(str(result_file))
            return results

        except subprocess.TimeoutExpired:
            print("❌ JMVA solver timed out")
            return {}
        except Exception as e:
            print(f"❌ Error running JMVA solver: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def _parse_jmva_results(self, result_path: str) -> Dict:
        """
        Parse JMVA results from the -result.jmva file.

        Args:
            result_path: Path to JMVA results file

        Returns:
            Dictionary with results per station {station_name: {metric: value}}
        """
        results = {}

        try:
            tree = ET.parse(result_path)
            root = tree.getroot()

            # Find solutions element
            solutions = root.find('.//solutions')
            if solutions is None:
                print("  ⚠️  No solutions found in JMVA results")
                return results

            # Iterate through stations
            for station_elem in solutions.findall('.//stationresults'):
                station_name = station_elem.get('station')
                if not station_name:
                    continue

                station_results = {}

                # Extract metrics for each class
                for class_results in station_elem.findall('.//classresults'):
                    # Get throughput
                    throughput_elem = class_results.find('throughput')
                    if throughput_elem is not None and throughput_elem.text:
                        station_results['throughput'] = float(throughput_elem.text)

                    # Get response time (residence time)
                    res_time_elem = class_results.find('residencetime')
                    if res_time_elem is not None and res_time_elem.text:
                        station_results['response_time'] = float(res_time_elem.text)

                    # Get utilization
                    util_elem = class_results.find('utilization')
                    if util_elem is not None and util_elem.text:
                        station_results['utilization'] = float(util_elem.text)

                    # Get queue length
                    queue_len_elem = class_results.find('queuelength')
                    if queue_len_elem is not None and queue_len_elem.text:
                        station_results['queue_length'] = float(queue_len_elem.text)

                if station_results:
                    results[station_name] = station_results

            print(f"  📊 Parsed {len(results)} station results from JMVA")

        except Exception as e:
            print(f"⚠️  Error parsing JMVA results: {e}")
            import traceback
            traceback.print_exc()

        return results

    def run_jmt_simulation(self, model_path: str, max_time: int = 120) -> Dict:
        """
        Run JMT simulation and extract results.

        Args:
            model_path: Path to JMT model file
            max_time: Maximum simulation time in seconds

        Returns:
            Dictionary with simulation results
        """
        print(f"🔬 Running JMT simulation...")

        try:
            # Run JMT in headless mode
            cmd = [
                'java', '-jar', str(self.jmt_jar_path),
                '-maxtime', str(max_time),
                model_path
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max_time + 60  # Give extra time for processing
            )

            if result.returncode != 0:
                print(f"⚠️  JMT simulation warning: {result.stderr}")

            # Parse results from output file
            # JMT creates output files in the same directory as the model
            model_dir = Path(model_path).parent
            model_name = Path(model_path).stem

            # Look for results file
            results = self._parse_jmt_results(model_path)

            return results

        except subprocess.TimeoutExpired:
            print("❌ JMT simulation timed out")
            return {}
        except Exception as e:
            print(f"❌ Error running JMT simulation: {e}")
            return {}

    def _parse_jmt_results(self, model_path: str) -> Dict:
        """
        Parse JMT results from the model file after simulation.

        Args:
            model_path: Path to JMT model file (contains results after simulation)

        Returns:
            Dictionary with results per station {station_name: {metric: value}}
        """
        results = {}

        try:
            tree = ET.parse(model_path)
            root = tree.getroot()

            # JMT stores results in <results> section at root level (after simulation)
            results_section = root.find('results')

            if results_section is not None:
                # Parse from <results> section (after JMT simulation completes)
                for measure in results_section.findall('measure'):
                    self._extract_measure_value(measure, results)
            else:
                # Fallback: try parsing from <sim> section (some JMT versions)
                sim = root.find('sim')
                if sim is not None:
                    for measure in sim.findall('measure'):
                        self._extract_measure_value(measure, results)

            if not results:
                print("  ⚠️  No results found in JMT output file")

        except Exception as e:
            print(f"⚠️  Error parsing JMT results: {e}")
            import traceback
            traceback.print_exc()

        return results

    def _extract_measure_value(self, measure: ET.Element, results: Dict):
        """
        Extract value from a measure element and store in results dict.

        Args:
            measure: XML measure element
            results: Dictionary to store results
        """
        # Get measure attributes
        name = measure.get('name', '')
        final_value = measure.get('finalValue')

        # Try different attribute names for station reference
        station = (
            measure.get('referenceStation') or
            measure.get('referenceNode') or
            ''
        )

        # Extract station name from measure name if not in attributes
        # Format: "StationName_ClassName_MetricType"
        if not station and '_' in name:
            parts = name.split('_')
            if len(parts) >= 2:
                station = parts[0]  # First part is usually station name

        if not final_value or not station:
            return

        try:
            value = float(final_value)

            # Initialize station dict if needed
            if station not in results:
                results[station] = {}

            # Determine metric type from name
            name_lower = name.lower()
            if 'throughput' in name_lower:
                results[station]['throughput'] = value
            elif 'response time' in name_lower or 'residence' in name_lower:
                results[station]['response_time'] = value
            elif 'utilization' in name_lower or 'utilisation' in name_lower:
                results[station]['utilization'] = value

        except (ValueError, AttributeError) as e:
            print(f"  ⚠️  Could not parse measure value: {e}")

    def validate_against_measurements(
        self,
        theoretical_results: Dict,
        measured_metrics: pd.DataFrame,
        mapping_config: Dict
    ) -> Dict:
        """
        Compare theoretical predictions with measured results per station.

        Automatically compares:
        - Throughput per station
        - Response time per station
        - CPU utilization per station

        Args:
            theoretical_results: Results from JMT simulation {station_name: {metric: value}}
            measured_metrics: DataFrame with measured metrics
            mapping_config: Mapping between JMT stations and K8s services

        Returns:
            Validation report with per-station errors and comparisons
        """
        validation = {
            'theoretical': {},
            'measured': {},
            'per_station': {},
            'errors': {},
            'summary': {}
        }

        # Get mapping configuration
        station_mapping = mapping_config.get('mapping', {})

        # Extract measured metrics per station (task)
        for deployment, config in station_mapping.items():
            # Skip delay stations and other non-task entries
            if deployment == 'delay' or config.get('station_type') == 'Delay':
                continue

            jmt_station = config.get('jmt_station', '')
            station_type = config.get('station_type', '')

            # Extract task/service name from deployment name
            # Support various naming patterns:
            # - "task1-deployment" -> "task1"
            # - "service-name-deployment" -> "service-name"
            # - "myservice" -> "myservice"
            if '-deployment' in deployment:
                task_name = deployment.replace('-deployment', '')
            else:
                task_name = deployment

            station_data = {
                'deployment': deployment,
                'jmt_station': jmt_station,
                'station_type': station_type,
                'theoretical': {},
                'measured': {},
                'errors': {}
            }

            # Extract measured metrics for this task
            # Try multiple column name patterns
            throughput_cols = [
                f'throughput_{task_name}',
                f'{task_name}_throughput',
                'throughput'
            ]
            rt_cols = [
                f'response_time_{task_name}',
                f'{task_name}_response_time',
                f'rt_{task_name}',
                'response_time_avg'
            ]
            cpu_cols = [
                f'cpu_usage_{task_name}',
                f'{task_name}_cpu',
                f'util_{task_name}'
            ]

            # Find and extract throughput
            for col in throughput_cols:
                if col in measured_metrics.columns:
                    station_data['measured']['throughput'] = measured_metrics[col].mean()
                    break

            # Find and extract response time
            for col in rt_cols:
                if col in measured_metrics.columns:
                    # Convert from ms to seconds if needed (values > 10 are likely in ms)
                    value = measured_metrics[col].mean()
                    if value > 10:
                        value = value / 1000.0
                    station_data['measured']['response_time'] = value
                    break

            # Find and extract CPU utilization
            for col in cpu_cols:
                if col in measured_metrics.columns:
                    station_data['measured']['utilization'] = measured_metrics[col].mean()
                    break

            # Extract theoretical metrics for this JMT station
            # Match by exact station name or fuzzy matching
            jmt_results = theoretical_results.get(jmt_station)
            if jmt_results:
                station_data['theoretical'] = jmt_results.copy()
            else:
                # Try fuzzy matching
                for station, metrics in theoretical_results.items():
                    if (jmt_station.lower() in station.lower() or
                        station.lower() in jmt_station.lower()):
                        station_data['theoretical'] = metrics.copy()
                        break

            # Calculate per-station errors
            for metric in ['throughput', 'response_time', 'utilization']:
                if metric in station_data['measured'] and metric in station_data['theoretical']:
                    measured_val = station_data['measured'][metric]
                    theoretical_val = station_data['theoretical'][metric]
                    if theoretical_val != 0:
                        relative_error = abs((measured_val - theoretical_val) / theoretical_val * 100)
                        station_data['errors'][f'{metric}_error_pct'] = relative_error

            validation['per_station'][task_name] = station_data

        # Calculate system-wide metrics
        if 'throughput' in measured_metrics.columns:
            validation['measured']['system_throughput'] = measured_metrics['throughput'].mean()

        if 'locust_response_time_avg' in measured_metrics.columns:
            validation['measured']['system_response_time'] = measured_metrics['locust_response_time_avg'].mean() / 1000.0

        # Calculate average errors across all stations
        all_throughput_errors = []
        all_rt_errors = []
        all_util_errors = []

        for task_name, data in validation['per_station'].items():
            if 'throughput_error_pct' in data['errors']:
                all_throughput_errors.append(data['errors']['throughput_error_pct'])
            if 'response_time_error_pct' in data['errors']:
                all_rt_errors.append(data['errors']['response_time_error_pct'])
            if 'utilization_error_pct' in data['errors']:
                all_util_errors.append(data['errors']['utilization_error_pct'])

        if all_throughput_errors:
            validation['errors']['avg_throughput_error_pct'] = sum(all_throughput_errors) / len(all_throughput_errors)
        if all_rt_errors:
            validation['errors']['avg_response_time_error_pct'] = sum(all_rt_errors) / len(all_rt_errors)
        if all_util_errors:
            validation['errors']['avg_utilization_error_pct'] = sum(all_util_errors) / len(all_util_errors)

        # Summary
        validation['summary'] = {
            'num_stations': len(validation['per_station']),
            'system_throughput': validation['measured'].get('system_throughput', 0),
            'system_response_time': validation['measured'].get('system_response_time', 0),
            'avg_throughput_error': validation['errors'].get('avg_throughput_error_pct', 0),
            'avg_rt_error': validation['errors'].get('avg_response_time_error_pct', 0),
            'avg_util_error': validation['errors'].get('avg_utilization_error_pct', 0),
        }

        # Overall assessment
        max_error = max(
            validation['errors'].get('avg_throughput_error_pct', 0),
            validation['errors'].get('avg_response_time_error_pct', 0),
            validation['errors'].get('avg_utilization_error_pct', 0)
        )

        if max_error < 10:
            validation['summary']['assessment'] = 'EXCELLENT'
        elif max_error < 20:
            validation['summary']['assessment'] = 'GOOD'
        elif max_error < 30:
            validation['summary']['assessment'] = 'ACCEPTABLE'
        else:
            validation['summary']['assessment'] = 'POOR'

        return validation

    def generate_validation_report(self, validation_results: Dict, output_path: str):
        """
        Generate a validation report comparing theory vs measurements per station.

        Args:
            validation_results: Results from validate_against_measurements
            output_path: Path to save the report
        """
        report = []
        report.append("=" * 60)
        report.append("JMT Model Validation Report")
        report.append("=" * 60)
        report.append("")

        # Per-station comparison
        report.append("## Per-Station Comparison")
        report.append("-" * 60)

        for task_name, data in validation_results.get('per_station', {}).items():
            report.append(f"\n### {task_name} <-> {data.get('jmt_station', 'N/A')}")
            report.append("")

            # Table header
            report.append("  Metric          | Measured      | Theoretical   | Error %")
            report.append("  " + "-" * 56)

            # Throughput
            if 'throughput' in data['measured'] or 'throughput' in data['theoretical']:
                meas = data['measured'].get('throughput', 0)
                theo = data['theoretical'].get('throughput', 0)
                err = data['errors'].get('throughput_error_pct', 0)
                report.append(f"  Throughput      | {meas:12.2f}  | {theo:12.2f}  | {err:6.2f}%")

            # Response Time
            if 'response_time' in data['measured'] or 'response_time' in data['theoretical']:
                meas = data['measured'].get('response_time', 0)
                theo = data['theoretical'].get('response_time', 0)
                err = data['errors'].get('response_time_error_pct', 0)
                report.append(f"  Response Time   | {meas:12.4f}s | {theo:12.4f}s | {err:6.2f}%")

            # Utilization
            if 'utilization' in data['measured'] or 'utilization' in data['theoretical']:
                meas = data['measured'].get('utilization', 0)
                theo = data['theoretical'].get('utilization', 0)
                err = data['errors'].get('utilization_error_pct', 0)
                report.append(f"  CPU Utilization | {meas:12.4f}  | {theo:12.4f}  | {err:6.2f}%")

        report.append("")

        # System-wide metrics
        report.append("## System-Wide Metrics")
        report.append("-" * 60)
        for key, value in validation_results.get('measured', {}).items():
            if 'time' in key:
                report.append(f"  {key}: {value:.4f} s")
            elif 'throughput' in key:
                report.append(f"  {key}: {value:.2f} req/s")
            else:
                report.append(f"  {key}: {value:.4f}")
        report.append("")

        # Average errors
        report.append("## Average Validation Errors")
        report.append("-" * 60)
        for key, value in validation_results.get('errors', {}).items():
            report.append(f"  {key}: {value:.2f}%")
        report.append("")

        # Summary
        report.append("## Summary")
        report.append("-" * 60)
        summary = validation_results.get('summary', {})
        report.append(f"  Number of stations: {summary.get('num_stations', 0)}")
        report.append(f"  System throughput: {summary.get('system_throughput', 0):.2f} req/s")
        report.append(f"  System response time: {summary.get('system_response_time', 0):.4f} s")
        report.append(f"  Avg throughput error: {summary.get('avg_throughput_error', 0):.2f}%")
        report.append(f"  Avg response time error: {summary.get('avg_rt_error', 0):.2f}%")
        report.append(f"  Avg utilization error: {summary.get('avg_util_error', 0):.2f}%")
        report.append("")
        report.append(f"  *** OVERALL ASSESSMENT: {summary.get('assessment', 'N/A')} ***")
        report.append("")

        # Save report
        with open(output_path, 'w') as f:
            f.write('\n'.join(report))

        print(f"✅ Validation report saved to: {output_path}")

        # Also print summary to console
        print(f"\n📊 Validation Summary:")
        print(f"  - Avg Throughput Error: {summary.get('avg_throughput_error', 0):.2f}%")
        print(f"  - Avg Response Time Error: {summary.get('avg_rt_error', 0):.2f}%")
        print(f"  - Avg Utilization Error: {summary.get('avg_util_error', 0):.2f}%")
        print(f"  - Overall Assessment: {summary.get('assessment', 'N/A')}")

        return '\n'.join(report)


def create_sample_mapping_config(output_path: str = "experiment_mapping_sample.yaml"):
    """
    Create a sample mapping configuration file for closed network.

    Args:
        output_path: Path to save the configuration
    """
    config = {
        'experiment': {
            'name': '3tier_closed_validation',
            'description': 'Validate 3-tier microservice architecture against Closed_3tier.jsimg template'
        },
        'jmt': {
            'template': 'JMT_Template/Closed_3tier.jsimg',
            'jar_path': '/Users/emilio-imt/JMT-singlejar-1.2.2.jar',
            'max_simulation_time': 120,
            'network_type': 'closed',
            'closed_network': {
                'customers': 1,
                'reference_station': 'Delay 1'
            }
        },
        'kubernetes': {
            'manifest': 'QNonK8s/applications/3tier.yaml',
            'namespace': 'default'
        },
        'mapping': {
            'delay': {
                'jmt_station': 'Delay 1',
                'station_type': 'Delay',
                'update_params': ['think_time_lambda']
            },
            'task1-deployment': {
                'jmt_station': 'Tier1',
                'station_type': 'Server',
                'service_time_env_var': 'SERVICE_TIME_SECONDS',
                'update_params': ['service_time_lambda', 'num_servers']
            },
            'task2-deployment': {
                'jmt_station': 'Tier2',
                'station_type': 'Server',
                'service_time_env_var': 'SERVICE_TIME_SECONDS',
                'update_params': ['service_time_lambda', 'num_servers']
            }
        },
        'workload': {
            'arrival_rate': 2.0,
            'think_time': 1.0,
            'distribution': 'exponential'
        },
        'validation': {
            'acceptable_error_threshold': 15.0,
            'metrics': ['throughput', 'response_time', 'utilization'],
            'generate_plots': True
        }
    }

    with open(output_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"✅ Sample mapping configuration created: {output_path}")
    return output_path


if __name__ == "__main__":
    # Demo: Create sample configuration
    print("Creating sample mapping configuration...")
    create_sample_mapping_config()

    # Test basic functionality
    print("\nTesting JMT Validator...")

    validator = JMTValidator()

    # Extract K8s parameters
    k8s_params = validator.extract_k8s_parameters('QNonK8s/applications/3tier.yaml')
    print(f"\nExtracted K8s parameters:")
    for service, params in k8s_params.items():
        print(f"  {service}:")
        print(f"    - replicas: {params['replicas']}")
        print(f"    - service_time: {params['service_time']}s")
        print(f"    - cpu_limit: {params['cpu_limit']} cores")
