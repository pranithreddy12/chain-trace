from typing import List, Dict, Any
from datetime import datetime
import json

from ..domain.models.investigation import InvestigationResult, CandidateEndpoint
from ..domain.models.graph import TransactionGraph
from ..domain.models.address import Address
from ..domain.models.transfer import Transfer
from ..domain.enums import Chain, EntityCategory


class ReportService:
    @staticmethod
    def generate_summary(result: InvestigationResult) -> Dict[str, Any]:
        inv = result.investigation
        seed = result.seed_address

        summary = {
            "investigation_id": inv.investigation_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "seed_address": {
                "address": seed.address,
                "chain": seed.chain.value,
                "label": seed.label,
            },
            "parameters": {
                "max_depth": inv.max_depth,
                "max_branches": inv.max_branches,
                "token_filter": inv.token_filter,
            },
            "statistics": {
                "transactions_examined": inv.transactions_examined,
                "nodes_discovered": inv.nodes_found,
                "edges_discovered": inv.edges_found,
                "max_depth_reached": max(
                    (n.depth for n in result.graph.nodes.values()), default=0
                ),
                "suspicious_patterns_detected": sum(
                    len(d) for d in result.pattern_detections.values()
                ),
                "candidate_endpoints": len(result.candidate_endpoints),
            },
            "case_summary": result.case_summary,
            "top_endpoints": [],
            "pattern_summary": {},
            "warnings": inv.warnings,
            "limitations": [
                "ChainTrace provides public-data-based investigative leads only",
                "Does not constitute legal determination or definitive attribution",
                "Mixer/bridge paths have reduced confidence",
                "Unknown addresses remain unclassified",
                "Exchange inference requires connection to known infrastructure",
            ],
            "disclaimer": "ChainTrace provides public-data-based investigative leads and does not constitute a legal determination or definitive attribution of ownership.",
        }

        for ep in result.candidate_endpoints[:10]:
            summary["top_endpoints"].append(
                {
                    "rank": len(summary["top_endpoints"]) + 1,
                    "address": ep.address.address,
                    "label": ep.address.label,
                    "entity_type": ep.address.entity_type.value,
                    "amount_received": ep.total_amount_received,
                    "hop_count": ep.hop_count,
                    "unlabeled_hops": ep.unlabeled_hop_count,
                    "confidence": f"{ep.confidence_score * 100:.1f}%",
                    "flow_confidence": f"{ep.flow_confidence * 100:.0f}%",
                    "entity_confidence": f"{ep.entity_confidence * 100:.0f}%",
                    "taint_fraction": f"{ep.taint_fraction * 100:.1f}%",
                    "is_terminal": ep.is_terminal,
                    "evidence": ep.evidence,
                    "pattern_flags": ep.pattern_flags,
                    "obfuscation_points": ep.obfuscation_points,
                }
            )

        for pattern_type, detections in result.pattern_detections.items():
            summary["pattern_summary"][pattern_type] = len(detections)

        return summary

    @staticmethod
    def generate_detailed_report(result: InvestigationResult) -> str:
        summary = ReportService.generate_summary(result)
        lines = [
            "=" * 60,
            "CHAINTRACE INVESTIGATIVE SUMMARY",
            "=" * 60,
            f"Investigation ID: {summary['investigation_id']}",
            f"Timestamp: {summary['timestamp']}",
            "",
            "SEED ADDRESS",
            "-" * 20,
            f"  Address: {summary['seed_address']['address']}",
            f"  Chain: {summary['seed_address']['chain']}",
            f"  Label: {summary['seed_address']['label'] or 'Unknown'}",
            "",
            "PARAMETERS",
            "-" * 20,
            f"  Max Depth: {summary['parameters']['max_depth']}",
            f"  Max Branches: {summary['parameters']['max_branches']}",
            f"  Token Filter: {summary['parameters']['token_filter'] or 'None'}",
            "",
            "STATISTICS",
            "-" * 20,
            f"  Transactions Examined: {summary['statistics']['transactions_examined']}",
            f"  Nodes Discovered: {summary['statistics']['nodes_discovered']}",
            f"  Edges Discovered: {summary['statistics']['edges_discovered']}",
            f"  Max Depth Reached: {summary['statistics']['max_depth_reached']}",
            f"  Suspicious Patterns: {summary['statistics']['suspicious_patterns_detected']}",
            f"  Candidate Endpoints: {summary['statistics']['candidate_endpoints']}",
            "",
        ]

        cs = summary.get("case_summary") or {}
        if cs:
            lines += ["INVESTIGATIVE FINDINGS", "-" * 20, f"  {cs.get('headline', '')}", ""]
            for f in cs.get("findings", []):
                lines.append(f"  [{f['severity'].upper()}] {f['title']}")
                lines.append(f"      {f['detail']}")
            lines += ["", "RECOMMENDED LEADS", "-" * 20]
            for i, l in enumerate(cs.get("recommended_leads", []), 1):
                tag = "VERIFIED" if l.get("verified") else "unverified"
                lines.append(f"  {i}. [{tag} {l['score']:.2f}] {l['address']}")
                lines.append(f"      {l['reason']}")
            lines.append("")

        lines += [
            "TOP INVESTIGATIVE ENDPOINTS",
            "-" * 20,
        ]

        for ep in summary["top_endpoints"]:
            lines.extend(
                [
                    f"  Rank {ep['rank']}: {ep['label'] or ep['address'][:8]}...{ep['address'][-6:]}",
                    f"    Address: {ep['address']}",
                    f"    Type: {ep['entity_type']}",
                    f"    Amount: {ep['amount_received']}",
                    f"    Hops: {ep['hop_count']} ({ep['unlabeled_hops']} unlabeled)",
                    f"    Confidence: {ep['confidence']}  "
                    f"(flow {ep['flow_confidence']} / entity {ep['entity_confidence']})",
                    f"    Share of reported funds reaching here: {ep['taint_fraction']}"
                    + ("  [TRAIL ENDS HERE]" if ep.get("is_terminal") else ""),
                    f"    Evidence: {', '.join(ep['evidence']) if ep['evidence'] else 'None'}",
                    f"    Patterns: {', '.join(ep['pattern_flags']) if ep['pattern_flags'] else 'None'}",
                    "",
                ]
            )

        lines.extend(
            [
                "PATTERN SUMMARY",
                "-" * 20,
            ]
        )

        for pattern, count in summary["pattern_summary"].items():
            lines.append(f"  {pattern}: {count} detections")

        lines.extend(
            [
                "",
                "WARNINGS",
                "-" * 20,
            ]
        )

        for warning in summary["warnings"]:
            lines.append(f"  - {warning}")

        if not summary["warnings"]:
            lines.append("  None")

        lines.extend(
            [
                "",
                "LIMITATIONS",
                "-" * 20,
            ]
        )

        for lim in summary["limitations"]:
            lines.append(f"  - {lim}")

        lines.extend(
            [
                "",
                "DISCLAIMER",
                "-" * 20,
                f"  {summary['disclaimer']}",
                "",
                "=" * 60,
                "END OF REPORT",
                "=" * 60,
            ]
        )

        return "\n".join(lines)

    @staticmethod
    def export_json(result: InvestigationResult) -> str:
        summary = ReportService.generate_summary(result)
        return json.dumps(summary, indent=2, default=str)
