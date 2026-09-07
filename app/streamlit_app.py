import sys
from pathlib import Path

# streamlit run puts app/ on sys.path, not the project root — add it so `src` imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
import pandas as pd

from src.config.settings import get_settings
from src.domain.enums import Chain, EntityCategory, NodeType, InvestigationStatus
from src.domain.models.address import Address
from src.application.investigation_service import InvestigationService
from src.application.report_service import ReportService
try:
    from graph_view import render_star_graph
except ModuleNotFoundError:
    from app.graph_view import render_star_graph


st.set_page_config(
    page_title="ChainTrace - Blockchain Fund-Flow Investigation",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_investigation_service():
    return InvestigationService()


def initialize_session_state():
    if "investigation_result" not in st.session_state:
        st.session_state.investigation_result = None
    if "selected_node" not in st.session_state:
        st.session_state.selected_node = None
    if "highlighted_path" not in st.session_state:
        st.session_state.highlighted_path = None
    if "trace_running" not in st.session_state:
        st.session_state.trace_running = False


def run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def render_header():
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("""
        # 🔍 CHAINTRACE
        ### Blockchain Fund-Flow Investigation Platform
        """)
    with col2:
        st.markdown(
            """
        <div style="text-align: right; padding-top: 20px;">
            <span style="color: #888;">Smart India Hackathon 2026</span>
        </div>
        """,
            unsafe_allow_html=True,
        )


def render_input_form():
    st.markdown("## Investigation Input")

    col1, col2, col3 = st.columns([3, 1, 1])

    with col1:
        seed_address = st.text_input(
            "Wallet Address",
            placeholder="0x... or T...",
            help="Enter the wallet address to investigate",
        )

    with col2:
        chain_option = st.selectbox(
            "Blockchain",
            options=[Chain.ETHEREUM, Chain.BSC, Chain.TRON],
            format_func=lambda x: {"bsc": "BNB CHAIN"}.get(x.value, x.value.upper()),
        )

    with col3:
        max_depth = st.slider(
            "Max Trace Depth",
            min_value=1,
            max_value=6,
            value=4,
            help="Maximum number of hops to trace",
        )

    col4, col5, col6 = st.columns([2, 1, 1])
    with col4:
        token_filter = st.text_input(
            "Token Filter (Optional)",
            placeholder="USDT contract address",
            help="Filter by specific token contract",
        )

    with col5:
        max_branches = st.number_input(
            "Max Branches",
            min_value=5,
            max_value=100,
            value=25,
            help="Maximum branches per node",
        )

    with col6:
        st.markdown("<br>", unsafe_allow_html=True)
        trace_button = st.button(
            "🔍 TRACE FUNDS",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.trace_running,
        )

    return {
        "seed_address": seed_address,
        "chain": chain_option,
        "max_depth": max_depth,
        "max_branches": max_branches,
        "token_filter": token_filter if token_filter else None,
        "trace_button": trace_button,
    }


def render_trace_status():
    if st.session_state.trace_running:
        with st.status("Tracing funds...", expanded=True) as status:
            st.write("🔄 Fetching transactions...")
            st.write("🔄 Building graph...")
            st.write("🔄 Analyzing patterns...")
            st.write("🔄 Matching entities...")
            st.write("🔄 Calculating confidence...")
            status.update(label="Investigation complete!", state="complete")



def render_main_graph(result):
    st.markdown("## Fund Flow Constellation")

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        if st.button("✨ Highlight strongest path", use_container_width=True):
            paths = result.graph.get_paths_to_endpoints(max_paths=1)
            if paths:
                st.session_state.highlighted_path = paths[0]
                st.rerun()
            else:
                st.toast("No candidate endpoint to trace a path to.")
    with c2:
        if st.button("Clear highlight", use_container_width=True):
            st.session_state.highlighted_path = None
            st.rerun()
    with c3:
        big = st.toggle("Expand graph", value=False, key="graph_big")

    render_star_graph(
        result,
        highlighted_path=st.session_state.highlighted_path,
        height=1000 if big else 660,
    )


def render_investigation_summary(result):
    st.markdown("## Investigation Summary")

    inv = result.investigation
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric("Nodes Discovered", inv.nodes_found)
    with col2:
        st.metric("Transactions Analyzed", inv.transactions_examined)
    with col3:
        st.metric(
            "Max Depth", max((n.depth for n in result.graph.nodes.values()), default=0)
        )
    with col4:
        st.metric(
            "Suspicious Patterns",
            sum(len(d) for d in result.pattern_detections.values()),
        )
    with col5:
        st.metric("Candidate Endpoints", len(result.candidate_endpoints))


_SEV_COLOR = {"high": "#ff4d4d", "medium": "#ffb020", "low": "#5fa8ff", "info": "#8494a8"}


def render_case_summary(result):
    cs = getattr(result, "case_summary", None)
    if not cs:
        return

    st.markdown("## Investigative Assessment")
    st.info(cs.get("headline", ""))

    ov = cs.get("overview", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sent by seed", f"{ov.get('total_sent_by_seed', 0):,.0f}")
    c2.metric("Direct recipients", ov.get("direct_recipients", 0))
    c3.metric("Wallets in graph", ov.get("wallets_in_graph", 0))
    c4.metric("Left observed window", f"{ov.get('value_left_observed_window', 0):,.0f}")

    leads = cs.get("recommended_leads", [])
    if leads:
        st.markdown("### Recommended leads")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "": "✔ verified" if l["verified"] else "unverified",
                        "Score": f"{l['score']:.2f}",
                        "Address": l["address"],
                        "Why": l["reason"],
                    }
                    for l in leads
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )

    findings = cs.get("findings", [])
    if findings:
        st.markdown("### Findings")
        for f in findings:
            color = _SEV_COLOR.get(f["severity"], "#8494a8")
            st.markdown(
                f'<span style="color:{color};font-weight:600">[{f["severity"].upper()}]'
                f'</span> &nbsp;<b>{f["title"]}</b><br>'
                f'<span style="color:#9fb2cc;font-size:0.9em">{f["detail"]}</span>',
                unsafe_allow_html=True,
            )
            st.markdown("")

    with st.expander("Limitations & disclaimer"):
        for lim in cs.get("limitations", []):
            st.markdown(f"- {lim}")
        st.caption(cs.get("disclaimer", ""))


def render_transactions_table(result):
    graph = result.graph
    st.markdown("## Transactions")

    if not graph.edges:
        st.info("No transfers were observed.")
        return

    rows = []
    for e in graph.edges:
        tr = e.transfer
        f = tr.normalized_from()
        rows.append(
            {
                "Date": tr.timestamp,
                "Depth": graph.nodes[f].depth if f in graph.nodes else None,
                "From": f,
                "To": tr.normalized_to(),
                "Amount": tr.amount_float,
                "Token": tr.token_symbol or "-",
                "Tx Hash": tr.transaction_hash,
                "On Path": bool(
                    st.session_state.highlighted_path
                    and f in st.session_state.highlighted_path
                    and tr.normalized_to() in st.session_state.highlighted_path
                ),
            }
        )

    df = pd.DataFrame(rows).sort_values("Date", ascending=False)

    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        tokens = sorted(t for t in df["Token"].unique())
        pick = st.multiselect("Token", tokens, default=tokens)
    with c2:
        min_amt = st.number_input("Min amount", min_value=0.0, value=0.0, step=1.0)
    with c3:
        search = st.text_input("Address contains", placeholder="paste an address fragment")

    view = df[df["Token"].isin(pick) & (df["Amount"] >= min_amt)]
    if search:
        s = search.strip().lower()
        view = view[
            view["From"].str.lower().str.contains(s)
            | view["To"].str.lower().str.contains(s)
        ]

    st.caption(
        f"{len(view)} of {len(df)} transfers · "
        f"{view['Amount'].sum():,.2f} total (filtered)"
    )
    st.dataframe(
        view,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Date": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
            "Amount": st.column_config.NumberColumn(format="%.4f"),
            "From": st.column_config.TextColumn(width="medium"),
            "To": st.column_config.TextColumn(width="medium"),
            "Tx Hash": st.column_config.TextColumn(width="medium"),
        },
    )


def render_endpoints_table(result):
    st.markdown("## Top Investigative Endpoints")

    if not result.candidate_endpoints:
        st.info("No candidate endpoints found.")
        return

    df_data = []
    for i, ep in enumerate(result.candidate_endpoints[:20]):
        df_data.append(
            {
                "Rank": i + 1,
                "Entity": ep.address.label
                or f"{ep.address.address[:8]}...{ep.address.address[-6:]}",
                "Address": ep.address.address,
                "Type": ep.address.entity_type.value,
                "Amount": ep.total_amount_received,
                "Hops": ep.hop_count,
                "Unlabeled": ep.unlabeled_hop_count,
                "Confidence": f"{ep.confidence_score * 100:.1f}%",
                "Evidence": "; ".join(ep.evidence[:2]) if ep.evidence else "—",
                "Patterns": ", ".join(ep.pattern_flags) if ep.pattern_flags else "—",
            }
        )

    df = pd.DataFrame(df_data)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Address": st.column_config.TextColumn(width="medium"),
            "Evidence": st.column_config.TextColumn(width="large"),
        },
    )

    selected_idx = st.selectbox(
        "Select endpoint for path inspection",
        range(len(result.candidate_endpoints[:10])),
        format_func=lambda i: (
            f"Rank {i + 1}: {result.candidate_endpoints[i].address.label or result.candidate_endpoints[i].address.address[:8]}..."
        ),
    )

    if selected_idx is not None:
        ep = result.candidate_endpoints[selected_idx]
        render_path_inspection(ep, result.graph)


def render_path_inspection(endpoint, graph):
    st.markdown("### Path Inspection")

    paths = graph.get_paths_to_endpoints(max_paths=3)
    if not paths:
        st.info("No path found to this endpoint.")
        return

    path = paths[0]

    for i, addr in enumerate(path):
        node = graph.nodes.get(addr)
        if node:
            col1, col2, col3 = st.columns([1, 3, 1])
            with col1:
                if i == 0:
                    st.markdown("🟢 **SEED**")
                elif i == len(path) - 1:
                    if node.address.entity_type == EntityCategory.EXCHANGE:
                        st.markdown("🔴 **EXCHANGE**")
                    elif node.address.entity_type == EntityCategory.SANCTIONED:
                        st.markdown("⚫ **SANCTIONED**")
                    else:
                        st.markdown("🔵 **ENDPOINT**")
                elif node.is_obfuscation_point:
                    st.markdown("🟡 **OBFUSCATION**")
                elif node.is_suspicious:
                    st.markdown("🟠 **SUSPICIOUS**")
                else:
                    st.markdown("🔵 **WALLET**")

            with col2:
                st.markdown(
                    f"**{node.address.display_name}**  \n"
                    f"`{node.address.address}`  \n"
                    f"Depth: {node.depth} | Type: {node.node_type.value}"
                )

            with col3:
                if i < len(path) - 1:
                    edge_amount = None
                    for edge in graph.edges:
                        if (
                            edge.transfer.normalized_from() == addr
                            and edge.transfer.normalized_to() == path[i + 1]
                        ):
                            edge_amount = (
                                f"{edge.transfer.amount} {edge.transfer.token_symbol}"
                            )
                            break
                    if edge_amount:
                        st.markdown(f"↓ **{edge_amount}**")

    if st.button("🎯 Highlight This Path", use_container_width=True):
        st.session_state.highlighted_path = path
        st.rerun()


def render_selected_node():
    if st.session_state.selected_node:
        node = st.session_state.selected_node
        st.markdown("## Selected Node Details")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Address:** `{node['address']}`")
            st.markdown(f"**Label:** {node['label'] or 'Unknown'}")
            st.markdown(f"**Entity Type:** {node['entity_type']}")
            st.markdown(f"**Node Type:** {node['type']}")
            st.markdown(f"**Depth:** {node['depth']}")

        with col2:
            st.markdown(f"**Incoming:** {node['incoming']}")
            st.markdown(f"**Outgoing:** {node['outgoing']}")
            st.markdown(f"**Confidence:** {node['confidence']:.2f}")
            st.markdown(f"**Patterns:** {', '.join(node['patterns']) or 'None'}")
            st.markdown(
                f"**Obfuscation Point:** {'Yes' if node['is_obfuscation'] else 'No'}"
            )

        st.markdown("---")


def render_report_section(result):
    st.markdown("## Generate Report")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("📄 Generate Investigative Summary", use_container_width=True):
            report = ReportService.generate_detailed_report(result)
            st.session_state.report_text = report

    with col2:
        if st.button("📥 Export JSON", use_container_width=True):
            json_report = ReportService.export_json(result)
            st.download_button(
                "Download JSON",
                json_report,
                f"chain_trace_{result.investigation.investigation_id[:8]}.json",
                "application/json",
                use_container_width=True,
            )

    if "report_text" in st.session_state:
        st.markdown("### Report Preview")
        st.code(st.session_state.report_text, language="text")

        st.download_button(
            "Download Report",
            st.session_state.report_text,
            f"chain_trace_report_{result.investigation.investigation_id[:8]}.txt",
            "text/plain",
            use_container_width=True,
        )


def main():
    initialize_session_state()
    render_header()

    settings = get_settings()

    if not settings.etherscan_api_key and not settings.trongrid_api_key:
        st.warning(
            "⚠️ No API keys configured. Please add ETHERSCAN_API_KEY and/or TRONGRID_API_KEY to your .env file. "
            "Some features may not work without valid API keys."
        )

    input_data = render_input_form()

    if input_data["trace_button"] and input_data["seed_address"]:
        st.session_state.trace_running = True
        st.session_state.investigation_result = None
        st.session_state.selected_node = None
        st.session_state.highlighted_path = None

        with st.spinner("Running investigation..."):
            try:
                service = get_investigation_service()
                result = run_async(
                    service.run_investigation(
                        seed_address=input_data["seed_address"],
                        chain=input_data["chain"],
                        max_depth=input_data["max_depth"],
                        max_branches=input_data["max_branches"],
                        token_filter=input_data["token_filter"],
                    )
                )
                st.session_state.investigation_result = result
                st.session_state.trace_running = False
                st.rerun()
            except Exception as e:
                st.session_state.trace_running = False
                st.error(f"Investigation failed: {str(e)}")
                st.rerun()

    if st.session_state.trace_running:
        render_trace_status()

    if st.session_state.investigation_result:
        result = st.session_state.investigation_result
        render_investigation_summary(result)
        render_case_summary(result)
        render_main_graph(result)
        render_selected_node()
        render_transactions_table(result)
        render_endpoints_table(result)
        render_report_section(result)


if __name__ == "__main__":
    main()
