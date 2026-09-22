import React, { useEffect, useState, useMemo } from "react";
import { BrowserRouter as Router, Routes, Route, Link, useParams, useNavigate } from "react-router-dom";
import "./index.css";

/* ==== API Helper ==== */
const API_BASE = "";

const api = {
  getChain: () => fetch(`${API_BASE}/api/chain`).then((r) => r.json()),
  getHealth: () => fetch(`${API_BASE}/api/health`).then((r) => r.json()),
  getTraces: () => fetch(`${API_BASE}/api/traces`).then((r) => r.json()),
  getTrace: (id: string) => fetch(`${API_BASE}/api/trace/${id}`).then((r) => r.json()),
  getAnomalies: () => fetch(`${API_BASE}/api/anomalies`).then((r) => r.json()),
  getIncidents: () => fetch(`${API_BASE}/api/incidents`).then((r) => r.json()),
  triggerHop: (faultRun = false, faultService?: string) => {
    // If faultService specified, update fault service first
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (faultRun) headers["X-Fault-Run"] = "true";
    return fetch(`${API_BASE}/hop`, {
      method: "POST",
      headers,
      body: JSON.stringify({}),
    }).then((r) => r.json());
  },
};

/* ==== Navbar Component ==== */
const Navbar: React.FC = () => {
  return (
    <nav className="navbar">
      <Link to="/" className="brand">
        <div className="brand-icon">TQ</div>
        <div>
          <div className="brand-title">TimeQuake</div>
          <div className="brand-subtitle">Causal Time-Travel Debugger</div>
        </div>
      </Link>
      <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
        <Link to="/" className="btn btn-sm">Dashboard</Link>
        <a href="https://github.com" target="_blank" rel="noreferrer" className="btn btn-sm">Docs</a>
      </div>
    </nav>
  );
};

/* ==== Anomaly Time-Series Chart ==== */
type AnomalyPoint = {
  trace_id: string;
  timestamp: number;
  service: string;
  z_score: number;
  is_fault_run: boolean;
  is_anomalous: boolean;
};

const AnomalyChart: React.FC<{ points: AnomalyPoint[] }> = ({ points }) => {
  const navigate = useNavigate();

  const width = 800;
  const height = 180;
  const padding = 30;

  const maxZ = useMemo(() => {
    const vals = points.map((p) => p.z_score);
    return Math.max(5.0, ...vals);
  }, [points]);

  const thresholdY = height - padding - ((2.0 / maxZ) * (height - 2 * padding));

  const coords = useMemo(() => {
    if (!points.length) return [];
    const minT = points[0].timestamp;
    const maxT = points[points.length - 1].timestamp || minT + 1;
    const tSpan = Math.max(maxT - minT, 1);

    return points.map((p) => {
      const x = padding + ((p.timestamp - minT) / tSpan) * (width - 2 * padding);
      const y = height - padding - ((p.z_score / maxZ) * (height - 2 * padding));
      return { ...p, x, y };
    });
  }, [points, maxZ]);

  return (
    <div className="card" style={{ marginBottom: "24px" }}>
      <div className="card-title">
        <span>Z-Score Anomaly Time-Series (Threshold z &gt; 2.0)</span>
        <span style={{ fontSize: "11px", color: "var(--accent-amber)" }}>
          {points.filter((p) => p.is_anomalous).length} Anomalous Request(s)
        </span>
      </div>

      <div style={{ position: "relative", width: "100%", overflowX: "auto" }}>
        <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "180px" }}>
          {/* Background grid */}
          <line x1={padding} y1={padding} x2={width - padding} y2={padding} stroke="var(--border-color)" strokeDasharray="3 3" />
          <line x1={padding} y1={height / 2} x2={width - padding} y2={height / 2} stroke="var(--border-color)" strokeDasharray="3 3" />

          {/* Threshold Line z = 2.0 */}
          <line
            x1={padding}
            y1={thresholdY}
            x2={width - padding}
            y2={thresholdY}
            stroke="var(--accent-amber)"
            strokeWidth="1.5"
            strokeDasharray="4 4"
          />
          <text x={width - padding - 80} y={thresholdY - 6} fill="var(--accent-amber)" fontSize="10" className="mono">
            Threshold (z = 2.0)
          </text>

          {/* Connective Line */}
          {coords.length > 1 && (
            <polyline
              fill="none"
              stroke="var(--accent-blue)"
              strokeWidth="1.5"
              opacity="0.6"
              points={coords.map((c) => `${c.x},${c.y}`).join(" ")}
            />
          )}

          {/* Data Points */}
          {coords.map((c, i) => (
            <g
              key={i}
              style={{ cursor: "pointer" }}
              onClick={() => navigate(`/trace/${c.trace_id}`)}
            >
              <circle
                cx={c.x}
                cy={c.y}
                r={c.is_anomalous ? 7 : 4}
                fill={c.is_anomalous ? "var(--accent-rose)" : "var(--accent-cyan)"}
                stroke={c.is_anomalous ? "#fff" : "none"}
                strokeWidth={1.5}
              />
              <title>{`Trace: ${c.trace_id}\nService: ${c.service}\nZ-Score: ${c.z_score}\nFault Run: ${c.is_fault_run}`}</title>
            </g>
          ))}
        </svg>
      </div>
    </div>
  );
};

/* ==== Microservices Status Panel ==== */
const MicroservicesPanel: React.FC<{
  services: string[];
  health: Record<string, any>;
  onTriggerFault: (svc: string) => void;
  loadingFault: boolean;
}> = ({ services, health, onTriggerFault, loadingFault }) => {
  const [selectedFaultSvc, setSelectedFaultSvc] = useState<string>("inventory");

  return (
    <div className="card">
      <div className="card-title">
        <span>Live Microservices Chain (10 Hops)</span>
        <div className="badge badge-up">Auto-refresh (4s)</div>
      </div>

      <div className="svc-grid" style={{ marginBottom: "20px" }}>
        {services.map((name, idx) => {
          const info = health[name] || {};
          const isUp = info.status === "up";
          const port = 5001 + idx;

          return (
            <div key={name} className="svc-card">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                <span className="svc-name">#{idx + 1} {name}</span>
                <span className={`badge ${isUp ? "badge-up" : "badge-down"}`}>
                  {isUp ? "UP" : "DOWN"}
                </span>
              </div>
              <div className="svc-meta mono">
                Port: {port}<br />
                Reqs: {info.request_count || 0}<br />
                Uptime: {info.uptime || 0}s
              </div>
            </div>
          );
        })}
      </div>

      {/* Demo Fault Injection Controller */}
      <div style={{ background: "var(--bg-subtle)", padding: "16px", borderRadius: "10px", border: "1px solid var(--border-color)" }}>
        <div style={{ fontSize: "13px", fontWeight: "600", marginBottom: "10px", color: "var(--accent-amber)" }}>
          Simulate Fault Injection Demo
        </div>
        <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
          <select
            className="btn btn-sm mono"
            value={selectedFaultSvc}
            onChange={(e) => setSelectedFaultSvc(e.target.value)}
            style={{ padding: "8px 12px", background: "var(--bg-card)" }}
          >
            {services.map((s) => (
              <option key={s} value={s}>
                Inject Fault into '{s}'
              </option>
            ))}
          </select>

          <button
            className="btn btn-rose btn-sm"
            disabled={loadingFault}
            onClick={() => onTriggerFault(selectedFaultSvc)}
          >
            {loadingFault ? "Running Request..." : "Execute Fault Run (X-Fault-Run: true)"}
          </button>
        </div>
      </div>
    </div>
  );
};

/* ==== Recent Incidents Feed ==== */
const IncidentFeed: React.FC<{ incidents: any[] }> = ({ incidents }) => {
  return (
    <div className="card">
      <div className="card-title">Recent Incidents Feed</div>
      <div style={{ display: "flex", flexDirection: "column", gap: "12px", maxHeight: "360px", overflowY: "auto" }}>
        {incidents.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: "13px" }}>No anomalies recorded yet. Run a fault test!</div>
        ) : (
          incidents.map((inc, i) => (
            <Link
              key={i}
              to={`/trace/${inc.trace_id}`}
              style={{
                textDecoration: "none",
                background: "var(--bg-subtle)",
                border: "1px solid var(--border-color)",
                borderRadius: "8px",
                padding: "12px",
                display: "block",
                transition: "all 0.2s",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px" }}>
                <span className="mono" style={{ fontSize: "12px", color: "var(--accent-cyan)", fontWeight: "600" }}>
                  {inc.trace_id}
                </span>
                <span className="badge badge-root">Root: {inc.service}</span>
              </div>
              <div style={{ fontSize: "12px", color: "var(--text-muted)", marginBottom: "4px" }}>
                Observed at symptom: <strong>{inc.symptom_service || "N/A"}</strong>
              </div>
              <div style={{ fontSize: "11px", color: "var(--text-dim)" }}>
                {new Date((inc.timestamp || 0) * 1000).toLocaleTimeString()}
              </div>
            </Link>
          ))
        )}
      </div>
    </div>
  );
};

/* ==== Dashboard View Component ==== */
const DashboardView: React.FC = () => {
  const [chain, setChain] = useState<string[]>([]);
  const [health, setHealth] = useState<Record<string, any>>({});
  const [anomalies, setAnomalies] = useState<AnomalyPoint[]>([]);
  const [incidents, setIncidents] = useState<any[]>([]);
  const [traces, setTraces] = useState<any[]>([]);
  const [loadingFault, setLoadingFault] = useState(false);

  const reloadData = () => {
    api.getChain().then((d) => setChain(d.services || []));
    api.getHealth().then((h) => setHealth(h));
    api.getAnomalies().then((a) => setAnomalies(a.anomalies || []));
    api.getIncidents().then((inc) => setIncidents(inc.incidents || []));
    api.getTraces().then((t) => setTraces(t.traces || []));
  };

  useEffect(() => {
    reloadData();
    const interval = setInterval(() => {
      api.getHealth().then((h) => setHealth(h));
    }, 4000);
    return () => clearInterval(interval);
  }, []);

  const handleTriggerFault = async (svc: string) => {
    setLoadingFault(true);
    try {
      const res = await api.triggerHop(true, svc);
      setTimeout(() => {
        reloadData();
        setLoadingFault(false);
      }, 800);
    } catch (e) {
      console.error(e);
      setLoadingFault(false);
    }
  };

  const handleNormalRun = async () => {
    setLoadingFault(true);
    try {
      await api.triggerHop(false);
      setTimeout(() => {
        reloadData();
        setLoadingFault(false);
      }, 500);
    } catch (e) {
      console.error(e);
      setLoadingFault(false);
    }
  };

  return (
    <div className="container">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "24px" }}>
        <div>
          <h1 style={{ fontSize: "28px", fontWeight: "700" }}>System Dashboard</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "14px" }}>
            Real-time causal graph monitoring & automated root-cause analysis
          </p>
        </div>
        <div style={{ display: "flex", gap: "10px" }}>
          <button className="btn btn-sm btn-cyan" onClick={handleNormalRun} disabled={loadingFault}>
            Send Normal Request
          </button>
        </div>
      </div>

      <AnomalyChart points={anomalies} />

      <div className="grid-main">
        <div>
          <MicroservicesPanel
            services={chain}
            health={health}
            onTriggerFault={handleTriggerFault}
            loadingFault={loadingFault}
          />

          {/* Recent Traces Table */}
          <div className="card" style={{ marginTop: "24px" }}>
            <div className="card-title">Recent Request Traces</div>
            <div className="table-container">
              <table className="table">
                <thead>
                  <tr>
                    <th>Trace ID</th>
                    <th>Initiator</th>
                    <th>Spans</th>
                    <th>Type</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {traces.map((t) => (
                    <tr key={t.trace_id}>
                      <td className="mono" style={{ color: "var(--accent-cyan)" }}>{t.trace_id}</td>
                      <td>{t.service}</td>
                      <td>{t.span_count} hops</td>
                      <td>
                        <span className={`badge ${t.is_fault_run ? "badge-down" : "badge-up"}`}>
                          {t.is_fault_run ? "FAULT RUN" : "NORMAL"}
                        </span>
                      </td>
                      <td>
                        <Link to={`/trace/${t.trace_id}`} className="btn btn-sm">
                          Replay &amp; RCA
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div>
          <IncidentFeed incidents={incidents} />
        </div>
      </div>
    </div>
  );
};

/* ==== Trace Detail & Replay View ==== */
const TraceDetailView: React.FC = () => {
  const { traceId } = useParams<{ traceId: string }>();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [currentStep, setCurrentStep] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  useEffect(() => {
    if (!traceId) return;
    setLoading(true);
    api.getTrace(traceId).then((res) => {
      setData(res);
      setLoading(false);
      setCurrentStep((res.nodes?.length || 1) - 1);
    });
  }, [traceId]);

  const maxSteps = (data?.nodes?.length || 1) - 1;

  useEffect(() => {
    let timer: any;
    if (isPlaying) {
      timer = setInterval(() => {
        setCurrentStep((prev) => {
          if (prev >= maxSteps) {
            setIsPlaying(false);
            return maxSteps;
          }
          return prev + 1;
        });
      }, 1000);
    }
    return () => clearInterval(timer);
  }, [isPlaying, maxSteps]);

  if (loading || !data) {
    return (
      <div className="container" style={{ marginTop: "40px" }}>
        <div className="card">Loading causal graph for trace {traceId}...</div>
      </div>
    );
  }

  const { nodes = [], edges = [], root_cause_service, symptom_service, cascade_path = [], rollback_events = [], explanation } = data;

  return (
    <div className="container">
      <div style={{ marginBottom: "20px" }}>
        <Link to="/" className="btn btn-sm" style={{ marginBottom: "12px" }}>
          &larr; Back to Dashboard
        </Link>
        <h1 style={{ fontSize: "24px", fontWeight: "700" }}>
          Trace Replay &amp; RCA: <span className="mono" style={{ color: "var(--accent-cyan)" }}>{traceId}</span>
        </h1>
      </div>

      {/* Root Cause Explanation Banner */}
      <div
        className="card"
        style={{
          borderLeft: `4px solid ${root_cause_service ? "var(--accent-amber)" : "var(--accent-emerald)"}`,
          background: root_cause_service ? "rgba(245, 158, 11, 0.08)" : "rgba(16, 185, 129, 0.08)",
          marginBottom: "24px",
        }}
      >
        <div style={{ display: "flex", gap: "12px", alignItems: "center", marginBottom: "8px" }}>
          {root_cause_service ? (
            <span className="badge badge-root">ROOT CAUSE FLAGGED: {root_cause_service}</span>
          ) : (
            <span className="badge badge-up">HEALTHY TRACE</span>
          )}
          {symptom_service && <span className="badge badge-symptom">Symptom Observed: {symptom_service}</span>}
        </div>
        <p style={{ fontSize: "14px", lineHeight: "1.6", color: "var(--text-main)" }}>
          {explanation}
        </p>
      </div>

      {/* Interactive Time-Travel Replayer */}
      <div className="card" style={{ marginBottom: "24px" }}>
        <div className="card-title">Interactive Time-Travel Replayer</div>
        <div className="scrubber-panel">
          <button className="btn btn-sm btn-cyan" onClick={() => setIsPlaying(!isPlaying)}>
            {isPlaying ? "Pause" : "Play Replay"}
          </button>
          <button className="btn btn-sm" onClick={() => setCurrentStep(Math.max(0, currentStep - 1))}>
            Step &larr;
          </button>
          <button className="btn btn-sm" onClick={() => setCurrentStep(Math.min(maxSteps, currentStep + 1))}>
            Step &rarr;
          </button>
          <input
            type="range"
            min="0"
            max={maxSteps}
            value={currentStep}
            onChange={(e) => setCurrentStep(parseInt(e.target.value))}
            className="slider"
          />
          <span className="mono" style={{ fontSize: "13px", color: "var(--accent-cyan)" }}>
            Hop {currentStep + 1} / {maxSteps + 1} ({nodes[currentStep]?.service})
          </span>
        </div>

        {/* Causal Graph SVG Visualization */}
        <div className="graph-container">
          <svg width="100%" height="100%" viewBox="0 0 1000 280">
            {/* Draw Horizontal Chain Edges */}
            {nodes.map((node: any, idx: number) => {
              if (idx === 0) return null;
              const x1 = 60 + (idx - 1) * 90;
              const x2 = 60 + idx * 90;
              const y = 140;
              const isPastStep = idx <= currentStep;

              return (
                <line
                  key={idx}
                  x1={x1}
                  y1={y}
                  x2={x2}
                  y2={y}
                  stroke={isPastStep ? (node.is_cascade ? "var(--accent-amber)" : "var(--accent-cyan)") : "var(--border-color)"}
                  strokeWidth={isPastStep ? 3 : 1.5}
                  strokeDasharray={isPastStep ? "none" : "4 4"}
                />
              );
            })}

            {/* Draw Service Hop Nodes */}
            {nodes.map((node: any, idx: number) => {
              const x = 60 + idx * 90;
              const y = 140;
              const isActive = idx === currentStep;
              const isPast = idx <= currentStep;

              let fill = "var(--bg-card)";
              let stroke = "var(--border-glow)";
              if (node.is_root_cause) {
                fill = "var(--accent-amber)";
                stroke = "#fff";
              } else if (node.is_symptom) {
                fill = "var(--accent-rose)";
                stroke = "#fff";
              } else if (node.is_anomalous) {
                fill = "var(--accent-blue)";
                stroke = "var(--accent-cyan)";
              } else if (isPast) {
                fill = "var(--accent-cyan)";
                stroke = "#fff";
              }

              return (
                <g key={node.span_id} transform={`translate(${x}, ${y})`}>
                  <circle
                    r={isActive ? 22 : 16}
                    fill={fill}
                    stroke={stroke}
                    strokeWidth={isActive ? 3 : 1.5}
                    style={{ transition: "all 0.3s ease" }}
                  />
                  <text
                    y={isActive ? 36 : 30}
                    textAnchor="middle"
                    fill="var(--text-main)"
                    fontSize="11"
                    fontWeight={isActive ? "700" : "500"}
                  >
                    {node.service}
                  </text>
                  <text y={isActive ? 48 : 42} textAnchor="middle" fill="var(--text-muted)" fontSize="9" className="mono">
                    z={node.z_score?.toFixed(2)}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      </div>

      {/* Rollback Compensating Events Panel */}
      {rollback_events.length > 0 && (
        <div className="card" style={{ marginBottom: "24px" }}>
          <div className="card-title">
            <span>Saga Compensating Rollback Events</span>
            <span className="badge badge-root">{rollback_events.length} Action(s) Triggered</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {rollback_events.map((rb: any, i: number) => (
              <div
                key={i}
                style={{
                  background: "rgba(245, 158, 11, 0.1)",
                  border: "1px solid rgba(245, 158, 11, 0.3)",
                  borderRadius: "8px",
                  padding: "12px 16px",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <strong style={{ color: "var(--accent-amber)" }}>[ROLLBACK] Service: '{rb.service}'</strong>
                  <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "2px" }}>
                    {rb.reason}
                  </div>
                </div>
                <span className="badge badge-root">Triggered by {rb.triggered_by_service}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Hop Table */}
      <div className="card">
        <div className="card-title">Hop Metrics Table (Self-Time Isolation &amp; Z-Scores)</div>
        <div className="table-container">
          <table className="table">
            <thead>
              <tr>
                <th>Hop #</th>
                <th>Service</th>
                <th>Total Duration</th>
                <th>Self Time</th>
                <th>Baseline Mean</th>
                <th>Baseline Stdev</th>
                <th>Z-Score</th>
                <th>Role / Status</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((node: any, idx: number) => {
                let roleStr = "Normal";
                let badgeCls = "badge-up";
                if (node.is_root_cause) {
                  roleStr = "ROOT CAUSE";
                  badgeCls = "badge-root";
                } else if (node.is_symptom) {
                  roleStr = "SYMPTOM";
                  badgeCls = "badge-symptom";
                } else if (node.is_cascade) {
                  roleStr = "CASCADE PATH";
                  badgeCls = "badge-cascade";
                }

                return (
                  <tr key={node.span_id} className={node.is_root_cause ? "highlight-rc" : ""}>
                    <td className="mono">{idx + 1}</td>
                    <td style={{ fontWeight: "600" }}>{node.service}</td>
                    <td className="mono">{node.duration_ms}ms</td>
                    <td className="mono" style={{ color: "var(--accent-cyan)", fontWeight: "600" }}>
                      {node.self_time_ms}ms
                    </td>
                    <td className="mono">{node.baseline_mean}ms</td>
                    <td className="mono">{node.baseline_stdev}ms</td>
                    <td className="mono" style={{ fontWeight: "700", color: node.z_score > 2.0 ? "var(--accent-rose)" : "var(--accent-emerald)" }}>
                      {node.z_score?.toFixed(2)}
                    </td>
                    <td>
                      <span className={`badge ${badgeCls}`}>{roleStr}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

/* ==== Main App Component ==== */
const App: React.FC = () => {
  return (
    <Router>
      <Navbar />
      <Routes>
        <Route path="/" element={<DashboardView />} />
        <Route path="/trace/:traceId" element={<TraceDetailView />} />
      </Routes>
    </Router>
  );
};

export default App;