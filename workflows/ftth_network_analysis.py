"""Workflow FTTH Network Analysis — orchestration du template éponyme.

Ce workflow:
1. Charge le template JSON.
2. Exécute les règles séquentiellement.
3. Gère les dépendances entre capabilities (ex: buffer avant spatial_aggregate).
"""

import json
from pathlib import Path
from typing import List, Optional

from gispulse.core.models import Dataset, Job, Rule
from gispulse.orchestration.runner import JobRunner
from gispulse.persistence.repository import InMemoryRepository


class FTTHNetworkAnalysisWorkflow:
    """Workflow pour analyser un réseau FTTH à partir d'un template."""

    def __init__(self, template_path: Path, input_dataset: Dataset):
        self.template_path = template_path
        self.input_dataset = input_dataset
        self.rules: List[Rule] = []
        self._load_template()

    def _load_template(self) -> None:
        """Charge les règles depuis le template JSON."""
        with open(self.template_path, encoding="utf-8") as f:
            template_rules = json.load(f)
        
        for rule_data in template_rules:
            # Remplacer 'network_check' par une capability existante si nécessaire
            if rule_data["capability"] == "network_check":
                rule_data["capability"] = "network_topology_check"  # Alternative dans network_topology.py
            
            self.rules.append(Rule(**rule_data))

    def run(self, output_path: Optional[Path] = None) -> Dataset:
        """Exécute le workflow et retourne le dataset enrichi."""
        repo = InMemoryRepository()
        repo.save_dataset(self.input_dataset)
        
        job = Job(
            name="ftth_network_analysis",
            rules=self.rules,
            input_dataset_id=self.input_dataset.id,
            output_dataset_id=self.input_dataset.id,  # In-place pour simplifier
        )
        
        runner = JobRunner(repo)
        runner.run(job)
        
        result = repo.get_dataset(self.input_dataset.id)
        if output_path:
            repo.export_dataset(result.id, output_path, format="gpkg")
        
        return result


def main():
    """Exemple d'utilisation en CLI."""
    import typer
    from gispulse.persistence.gpkg import read_gpkg
    
    app = typer.Typer()
    
    @app.command()
    def execute(
        input_path: Path = typer.Argument(..., help="Chemin vers le GPKG d'entrée"),
        template_path: Path = typer.Argument(..., help="Chemin vers le template JSON"),
        output_path: Optional[Path] = typer.Option(None, "--output", "-o", help="Chemin de sortie (GPKG)"),
    ) -> None:
        dataset = read_gpkg(input_path)
        workflow = FTTHNetworkAnalysisWorkflow(template_path, dataset)
        result = workflow.run(output_path)
        typer.echo(f"Workflow terminé. Résultat: {len(result.layers)} couches.")
    
    app()