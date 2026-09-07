import { useState } from "react";

interface Props {
  investigation_id: string;
}

export default function ReportPanel({ investigation_id }: Props) {
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const generate = async () => {
    setLoading(true);
    setReport(null);
    try {
      const { report: text } = await fetch(
        `/api/report/${investigation_id}?format=text`
      ).then((r) => r.json());
      setReport(text);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const copy = async () => {
    if (!report) return;
    try {
      await navigator.clipboard.writeText(report);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch (_) {}
  };

  const download = () => {
    if (!report) return;
    const blob = new Blob([report], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `chaintrace-${investigation_id.slice(0, 8)}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="report-panel">
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
        <button
          className="btn"
          onClick={generate}
          disabled={loading || !investigation_id}
        >
          {loading ? "generating…" : "Generate Investigative Summary"}
        </button>
        <span className="tiny">plain-text investigative report</span>
      </div>

      {report ? (
        <>
          <pre>{report}</pre>
          <div className="report-actions">
            <button className="btn" onClick={copy}>
              {copied ? "copied ✓" : "copy"}
            </button>
            <button className="btn" onClick={download}>
              download .txt
            </button>
          </div>
        </>
      ) : (
        <div className="empty-state">Run a trace first to generate an investigative report.</div>
      )}
    </div>
  );
}
