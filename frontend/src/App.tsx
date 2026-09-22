import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BrowserRouter as Router, Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import "./index.css";

const API_BASE = "";
const POLL_INTERVAL_MS = 5000;
const LIVE_POLL_INTERVAL_MS = 1000;

const api = {
  getChain: () => fetch(`${API_BASE}/api/chain`).then((response) => response.json()),
  getHealth: () => fetch(`${API_BASE}/api/health`).then((response) => response.json()),
  getTraces: () => fetch(`${API_BASE}/api/traces`).then((response) => response.json()),
  getTrace: (id: string) => fetch(`${API_BASE}/api/trace/${id}`).then((response) => response.json()),
  getAnomalies: () => fetch(`${API_BASE}/api/anomalies`).then((response) => response.json()),
  getIncidents: () => fetch(`${API_BASE}/api/incidents`).then((response) => response.json()),
  getFaults: () => fetch(`${API_BASE}/api/fault`).then((response) => response.json()),
  getMonitorEvents: (service: string) => fetch(`${API_BASE}/api/monitor-events?service=${encodeURIComponent(service)}`).then((response) => response.json()),
  saveMonitorConfig: (service: string, config: any) => fetch(`${API_BASE}/api/service/${encodeURIComponent(service)}/monitor-config`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config) }).then((response) => response.json()),
  setOverride: (service: string, state: "auto" | "normal" | "error") => fetch(`${API_BASE}/api/service/${encodeURIComponent(service)}/override`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state }) }).then((response) => response.json()),
  triggerHop: () => fetch(`${API_BASE}/hop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  }).then((response) => response.json()),
  armFault: (service: string, delayMs: number, requestCount = 1) => fetch(`${API_BASE}/api/fault`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service, delay_ms: delayMs, request_count: requestCount }),
  }).then(async (response) => {
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Unable to arm live fault");
    return body;
  }),
};

type AnomalyPoint = {
  trace_id: string;
  span_id: string;
  timestamp: number;
  service: string;
  z_score: number;
  is_fault_run: boolean;
  is_anomalous: boolean;
};

const Navbar: React.FC = () => (
  <nav className="navbar">
    <Link to="/" className="brand">
      <div className="brand-icon">TQ</div>
      <div>
        <div className="brand-title">TimeQuake</div>
        <div className="brand-subtitle">Causal Time-Travel Debugger</div>
      </div>
    </Link>
    <Link to="/" className="btn btn-sm">Live dashboard</Link>
  </nav>
);

const METRIC_LABELS: Record<string, string> = { over_max_count: "Over-Max count", delta_of_maximum: "Delta-of-Maximum", over_average_count: "Over-Average count", max_moving_average: "Max-moving-average" };

const ServiceSidebar: React.FC<{ services: string[]; health: Record<string, any>; selected: string; onSelect: (service: string) => void }> = ({ services, health, selected, onSelect }) => (
  <aside className="service-sidebar"><div className="sidebar-title">Services</div>{services.map((service) => {
    const state = health[service]?.monitor_state || "normal";
    return <button key={service} className={`service-tab ${selected === service ? "selected" : ""}`} onClick={() => onSelect(service)}><i className={`status-dot ${state}`} /><span>{service}</span><small>{state.replace("_", " ")}</small></button>;
  })}</aside>
);

const CombinedMetricChart: React.FC<{ service: string; points: AnomalyPoint[]; events: any[]; rollbacks: any[] }> = ({ service, points, events, rollbacks }) => {
  const [zoom, setZoom] = useState(100);
  const visible = useMemo(() => points.slice(-Math.max(12, Math.ceil(points.length * zoom / 100))), [points, zoom]);
  const width = Math.max(760, visible.length * 42);
  const height = 250; const pad = { top: 24, right: 24, bottom: 30, left: 34 };
  const values = visible.map((point) => point.z_score); const min = Math.min(-1, ...values) - .25; const max = Math.max(3, ...values) + .25; const range = Math.max(max - min, 1);
  const y = (value: number) => height - pad.bottom - ((value - min) / range) * (height - pad.top - pad.bottom);
  const x = (index: number) => pad.left + (visible.length <= 1 ? (width - pad.left - pad.right) / 2 : index * (width - pad.left - pad.right) / (visible.length - 1));
  const currentIndex = Math.max(0, visible.length - Math.min(8, visible.length));
  return <section className="card combined-chart-panel"><div className="chart-toolbar"><div><div className="card-title">{service} · combined signal timeline</div><span className="chart-subtitle">Line: current z-score · whiskers: per-bucket min / max · tick: average</span></div><label className="zoom-control">Zoom <input type="range" min="25" max="100" step="25" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /> {zoom}%</label></div>
    {!visible.length ? <div className="empty-chart">Awaiting completed service spans</div> : <div className="combined-chart-scroll"><svg viewBox={`0 0 ${width} ${height}`} className="combined-chart" aria-label={`${service} live combined metrics chart`}>
      {[0, .5, 1].map((fraction) => <line key={fraction} x1={pad.left} y1={pad.top + fraction * (height - pad.top - pad.bottom)} x2={width - pad.right} y2={pad.top + fraction * (height - pad.top - pad.bottom)} className="chart-grid" />)}
      {visible.map((point, index) => point.is_anomalous && <rect key={`a-${point.span_id}`} x={x(index) - 13} y={pad.top} width="26" height={height - pad.top - pad.bottom} className="anomaly-region" />)}
      <line x1={x(currentIndex)} y1={pad.top} x2={x(currentIndex)} y2={height - pad.bottom} className="current-divider" /><text x={x(currentIndex) + 5} y={pad.top + 10} className="current-label">CURRENT</text><text x={pad.left} y={pad.top + 10} className="past-label">PAST</text>
      {visible.map((point, index) => { const bucket = visible.slice(Math.max(0, index - 1), Math.min(visible.length, index + 2)).map((entry) => entry.z_score); const low = Math.min(...bucket); const high = Math.max(...bucket); const avg = bucket.reduce((sum, value) => sum + value, 0) / bucket.length; return <g key={point.span_id}><line x1={x(index)} y1={y(low)} x2={x(index)} y2={y(high)} className="ohlc-whisker" /><line x1={x(index) - 6} y1={y(avg)} x2={x(index) + 6} y2={y(avg)} className="ohlc-average" /></g>; })}
      {visible.length > 1 && <polyline points={visible.map((point, index) => `${x(index)},${y(point.z_score)}`).join(" ")} className="combined-line" />}
      {visible.map((point, index) => <circle key={`p-${point.span_id}`} cx={x(index)} cy={y(point.z_score)} r={point.is_anomalous ? 4.5 : 2.8} className={point.is_anomalous ? "combined-dot anomaly" : "combined-dot"}><title>{`${new Date(point.timestamp * 1000).toLocaleTimeString()} · z ${point.z_score.toFixed(2)}`}</title></circle>)}
      {rollbacks.filter((rollback) => rollback.service === service).map((rollback) => { const nearest = visible.reduce((best, point, index) => Math.abs(point.timestamp - rollback.timestamp) < Math.abs(visible[best].timestamp - rollback.timestamp) ? index : best, 0); return <g key={rollback.id}><line x1={x(nearest)} y1={pad.top} x2={x(nearest)} y2={height - pad.bottom} className="rollback-marker" /><text x={x(nearest) + 4} y={height - 12} className="rollback-label">Rolled back at {new Date(rollback.timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</text></g>; })}
    </svg></div>}
    <div className="chart-footer"><span>{visible.length} samples</span><span>{events.filter((event) => event.status === "anomaly").length} automatic signals</span><span>scroll timeline to inspect historical buckets</span></div></section>;
};

const CausalGraph: React.FC<{ nodes: any[]; edges: any[]; currentStep?: number; live?: boolean; liveStates?: Record<string, any> }> = ({ nodes, edges, currentStep, live = false, liveStates = {} }) => {
  const width = Math.max(760, nodes.length * 118 + 100);
  const activeStep = currentStep ?? Math.max(nodes.length - 1, 0);
  const positions = new Map(nodes.map((node, index) => [node.span_id, 58 + index * 118]));

  if (!nodes.length) return <div className="empty-graph">Waiting for the first completed request…</div>;

  return (
    <div className="graph-container">
      <svg width="100%" height="100%" viewBox={`0 0 ${width} 280`}>
        {edges.map((edge: any) => {
          const from = positions.get(edge.from);
          const to = positions.get(edge.to);
          const isPast = (nodes.findIndex((node) => node.span_id === edge.to) <= activeStep);
          if (from === undefined || to === undefined) return null;
          return <line key={`${edge.from}-${edge.to}`} x1={from} y1={132} x2={to} y2={132} className={isPast ? "graph-edge active" : "graph-edge"} />;
        })}
        {nodes.map((node: any, index: number) => {
          const x = positions.get(node.span_id) || 0;
          const isActive = index === activeStep;
          const isPast = index <= activeStep;
          const rollbackStatus = node.rollback_status;
          const liveState = liveStates[node.service]?.transaction_state || node.current_state || {};
          let nodeClass = "graph-node";
          if (node.is_root_cause) nodeClass += " root-cause";
          else if (node.is_symptom) nodeClass += " symptom";
          else if (node.is_anomalous) nodeClass += " anomalous";
          else if (isPast) nodeClass += " traversed";
          if (liveState.status === "degraded") nodeClass += " degraded";

          return (
            <g key={node.span_id} transform={`translate(${x}, 132)`}>
              {rollbackStatus && <circle r={isActive ? 28 : 23} className={`rollback-ring ${rollbackStatus}`} />}
              <circle r={isActive ? 20 : 16} className={nodeClass} />
              {rollbackStatus && <text y={5} textAnchor="middle" className="rollback-mark">↶</text>}
              <text y={isActive ? 38 : 33} textAnchor="middle" className="graph-service-label">{node.service}</text>
              <text y={isActive ? 51 : 45} textAnchor="middle" className="graph-metric-label">z={node.z_score?.toFixed(2)}</text>
              {live && <text y={isActive ? 63 : 57} textAnchor="middle" className="graph-state-label">{liveState.status || node.state_transition || "idle"}</text>}
              {rollbackStatus && <text y={isActive ? -35 : -30} textAnchor="middle" className={`graph-rollback-label ${rollbackStatus}`}>{rollbackStatus}</text>}
            </g>
          );
        })}
      </svg>
      {live && <div className="graph-live-note"><i /> Polling server state</div>}
    </div>
  );
};

const MicroservicesPanel: React.FC<{
  services: string[];
  onNormalRequest: () => void;
  onLiveFault: (service: string, delayMs: number, requestCount: number, dispatchNow: boolean) => void;
  action: string | null;
}> = ({ services, onNormalRequest, onLiveFault, action }) => {
  const [selectedService, setSelectedService] = useState("");
  const [delayMs, setDelayMs] = useState(1500);
  const [requestCount, setRequestCount] = useState(1);
  const [dispatchNow, setDispatchNow] = useState(true);

  useEffect(() => {
    if (!selectedService || !services.includes(selectedService)) setSelectedService(services[0] || "");
  }, [services, selectedService]);

  return (
    <section className="card panel-spaced">
      <div className="fault-controller">
        <div>
          <div className="fault-controller-title">Live fault trigger</div>
          <p>Choose the latency spike and how many real requests it affects. Arm it for natural traffic, or dispatch a request immediately—nothing is configured at startup.</p>
        </div>
        <div className="fault-controls">
          <select className="control-field mono" value={selectedService} onChange={(event) => setSelectedService(event.target.value)}>
            {services.map((service) => <option key={service} value={service}>{service}</option>)}
          </select>
          <input className="control-field mono delay-field" type="number" min="1" max="60000" value={delayMs} onChange={(event) => setDelayMs(Number(event.target.value))} aria-label="Fault delay in milliseconds" />
          <span className="field-suffix">ms delay</span>
          <input className="control-field mono count-field" type="number" min="1" max="25" value={requestCount} onChange={(event) => setRequestCount(Number(event.target.value))} aria-label="Requests to affect" />
          <span className="field-suffix">request(s)</span>
          <label className="dispatch-toggle"><input type="checkbox" checked={dispatchNow} onChange={(event) => setDispatchNow(event.target.checked)} /> send now</label>
          <button className="btn btn-rose btn-sm" disabled={!selectedService || Boolean(action)} onClick={() => onLiveFault(selectedService, delayMs, requestCount, dispatchNow)}>
            {action === "fault" ? "Arming…" : dispatchNow ? "Arm & send" : "Arm fault"}
          </button>
          <button className="btn btn-cyan btn-sm" disabled={Boolean(action)} onClick={onNormalRequest}>
            {action === "normal" ? "Sending…" : "Send normal request"}
          </button>
        </div>
      </div>
    </section>
  );
};

const IncidentFeed: React.FC<{ incidents: any[] }> = ({ incidents }) => (
  <aside className="card panel-spaced incident-card">
    <div className="card-title">Recent incidents</div>
    <div className="incident-list">
      {incidents.length === 0 ? <div className="empty-state">No anomalous traces yet.</div> : incidents.map((incident) => (
        <Link key={incident.trace_id} to={`/trace/${incident.trace_id}`} className="incident-item">
          <div><span className="mono incident-id">{incident.trace_id}</span><span className="badge badge-root">{incident.service}</span></div>
          <p>Symptom: {incident.symptom_service || "awaiting diagnosis"}</p>
          <time>{new Date((incident.timestamp || 0) * 1000).toLocaleTimeString()}</time>
        </Link>
      ))}
    </div>
  </aside>
);

const eventLabel = (eventType: string) => ({
  state_applied: "State applied",
  fault_detected: "Fault observed",
  rollback_scheduled: "Rollback scheduled",
  rollback_completed: "State restored",
}[eventType] || eventType.replaceAll("_", " "));

const StateSnapshot: React.FC<{ title: string; state: any; variant: "before" | "after" }> = ({ title, state, variant }) => (
  <div className={`state-snapshot ${variant}`}>
    <span>{title}</span>
    <strong>{state?.status || "idle"}</strong>
    <div className="mono"><b>{state?.operation_count ?? 0}</b> applied</div>
    <div className="mono"><b>{state?.rollback_count ?? 0}</b> restored</div>
  </div>
);

const RollbackPlayback: React.FC<{ events: any[] }> = ({ events }) => {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const boundedStep = Math.min(step, Math.max(events.length - 1, 0));
  const event = events[boundedStep];

  useEffect(() => setStep((current) => Math.min(current, Math.max(events.length - 1, 0))), [events.length]);
  useEffect(() => {
    if (!playing || events.length < 2) return undefined;
    const timer = window.setInterval(() => setStep((current) => {
      if (current >= events.length - 1) { setPlaying(false); return events.length - 1; }
      return current + 1;
    }), 1000);
    return () => window.clearInterval(timer);
  }, [playing, events.length]);

  if (!event) return null;
  const restoration = event.event_type === "rollback_completed";
  return <section className="card panel-spaced playback-card">
    <div className="card-title"><span>Persisted state restoration playback</span><span className="live-label"><i /> Replays database snapshots</span></div>
    <p className="playback-copy">Move through the real state events captured for this trace. A completed rollback visibly returns the affected service from its pre-compensation state to its restored state.</p>
    <div className="playback-controls"><button className="btn btn-sm btn-cyan" onClick={() => setPlaying(!playing)}>{playing ? "Pause" : "Play state changes"}</button><button className="btn btn-sm" onClick={() => setStep(Math.max(0, boundedStep - 1))}>← Previous</button><button className="btn btn-sm" onClick={() => setStep(Math.min(events.length - 1, boundedStep + 1))}>Next →</button><input className="slider" type="range" min="0" max={Math.max(events.length - 1, 0)} value={boundedStep} onChange={(entry) => setStep(Number(entry.target.value))} /><span className="mono replay-position">{boundedStep + 1}/{events.length}</span></div>
    <div className={`state-event-focus ${event.event_type}`}><div><span className="event-kicker">{event.service}</span><h3>{eventLabel(event.event_type)}</h3><p>{new Date(event.timestamp * 1000).toLocaleTimeString()} · span {event.span_id}</p></div><span className={`badge ${restoration ? "badge-up" : event.event_type === "rollback_scheduled" ? "badge-root" : "badge-symptom"}`}>{restoration ? "previous state restored" : eventLabel(event.event_type)}</span></div>
    <div className="state-transition"><StateSnapshot title={restoration ? "Before rollback" : "Previous state"} state={event.previous_state} variant="before" /><div className={`state-arrow ${restoration ? "reverse" : ""}`}>{restoration ? "↶" : "→"}<small>{restoration ? "compensate" : "apply"}</small></div><StateSnapshot title={restoration ? "Restored state" : "Current state"} state={event.current_state} variant="after" /></div>
    <div className="event-rail">{events.map((item, index) => <button key={item.id} className={`event-dot ${index === boundedStep ? "selected" : ""} ${item.event_type}`} onClick={() => setStep(index)} title={`${item.service}: ${eventLabel(item.event_type)}`}><span>{index + 1}</span></button>)}</div>
  </section>;
};

const TopStatusBar: React.FC<{ health: Record<string, any>; now: number }> = ({ health, now }) => {
  const states = Object.values(health).reduce((counts: Record<string, number>, item: any) => { counts[item.monitor_state || "normal"] = (counts[item.monitor_state || "normal"] || 0) + 1; return counts; }, {});
  return <div className="top-status-bar"><span><i className="status-dot normal" /> {states.normal || 0} normal</span><span><i className="status-dot anomaly" /> {states.anomaly || 0} anomalous</span><span><i className="status-dot rolling_back" /> {states.rolling_back || 0} rolling back</span><time className="mono">{new Date(now).toLocaleString()}</time></div>;
};

const ServiceConfigPanel: React.FC<{ service: string; config: any; onSave: (config: any) => void; saving: boolean }> = ({ service, config, onSave, saving }) => {
  const [draft, setDraft] = useState(config);
  useEffect(() => setDraft(config), [config, service]);
  if (!draft) return null;
  return <section className="card config-panel"><div className="card-title"><span>{service} · detection policy</span><span className="mono">SQLite persisted</span></div><div className="metric-config-list">{Object.entries(METRIC_LABELS).map(([metric, label]) => <div className="metric-config" key={metric}><label><input type="checkbox" checked={Boolean(draft.active_metrics?.[metric])} onChange={(event) => setDraft({ ...draft, active_metrics: { ...draft.active_metrics, [metric]: event.target.checked } })} /> {label}</label><input className="control-field mono" type="number" step="0.05" min="0" value={draft.thresholds?.[metric] ?? 1} onChange={(event) => setDraft({ ...draft, thresholds: { ...draft.thresholds, [metric]: Number(event.target.value) } })} /></div>)}</div><div className="config-bottom"><label>Coverage <input className="control-field mono" type="number" min="1" max="100" value={draft.coverage_percent} onChange={(event) => setDraft({ ...draft, coverage_percent: Number(event.target.value) })} />%</label><label className="dispatch-toggle"><input type="checkbox" checked={Boolean(draft.auto_rollback)} onChange={(event) => setDraft({ ...draft, auto_rollback: event.target.checked })} /> auto-rollback</label><button className="btn btn-cyan btn-sm" disabled={saving} onClick={() => onSave(draft)}>{saving ? "Saving…" : "Save policy"}</button></div></section>;
};

const DashboardView: React.FC = () => {
  const [chain, setChain] = useState<string[]>([]);
  const [health, setHealth] = useState<Record<string, any>>({});
  const [anomalies, setAnomalies] = useState<AnomalyPoint[]>([]);
  const [incidents, setIncidents] = useState<any[]>([]);
  const [traces, setTraces] = useState<any[]>([]);
  const [liveTrace, setLiveTrace] = useState<any>(null);
  const [faults, setFaults] = useState<any[]>([]);
  const [selectedService, setSelectedService] = useState("");
  const [monitorEvents, setMonitorEvents] = useState<any[]>([]);
  const [now, setNow] = useState(Date.now());
  const [savingConfig, setSavingConfig] = useState(false);
  const [action, setAction] = useState<string | null>(null);
  const [error, setError] = useState("");
  const dashboardRequestVersion = useRef(0);
  const latestTraceRequestVersion = useRef(0);

  const refreshLatestTrace = useCallback(async () => {
    const requestVersion = ++latestTraceRequestVersion.current;
    try {
      const traceData = await api.getTraces();
      if (requestVersion !== latestTraceRequestVersion.current) return;
      setTraces(traceData.traces || []);
      const newestTrace = traceData.traces?.[0];
      if (newestTrace?.trace_id) {
        const newestTraceDetail = await api.getTrace(newestTrace.trace_id);
        if (requestVersion !== latestTraceRequestVersion.current) return;
        setLiveTrace(newestTraceDetail);
      } else {
        setLiveTrace(null);
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to reach the latest trace");
    }
  }, []);

  const reloadData = useCallback(async () => {
    const requestVersion = ++dashboardRequestVersion.current;
    try {
      const [chainData, healthData, anomalyData, incidentData, faultData] = await Promise.all([
        api.getChain(), api.getHealth(), api.getAnomalies(), api.getIncidents(), api.getFaults(),
      ]);
      if (requestVersion !== dashboardRequestVersion.current) return;
      const configuredServices = chainData.services || [];
      setChain(configuredServices);
      setHealth(healthData || {});
      setAnomalies(anomalyData.anomalies || []);
      setIncidents(incidentData.incidents || []);
      setFaults(faultData.faults || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to reach the live server");
    }
  }, []);

  const refreshLiveState = useCallback(async () => {
    try {
      const [healthData, faultData] = await Promise.all([api.getHealth(), api.getFaults()]);
      setHealth(healthData || {});
      setFaults(faultData.faults || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to reach the live server");
    }
  }, []);

  useEffect(() => {
    reloadData();
    refreshLatestTrace();
    const dashboardInterval = window.setInterval(reloadData, POLL_INTERVAL_MS);
    const liveInterval = window.setInterval(refreshLiveState, LIVE_POLL_INTERVAL_MS);
    const traceInterval = window.setInterval(refreshLatestTrace, LIVE_POLL_INTERVAL_MS);
    return () => { window.clearInterval(dashboardInterval); window.clearInterval(liveInterval); window.clearInterval(traceInterval); };
  }, [reloadData, refreshLatestTrace, refreshLiveState]);

  useEffect(() => { if (!selectedService && chain[0]) setSelectedService(chain[0]); }, [chain, selectedService]);
  useEffect(() => {
    if (!selectedService) return undefined;
    const load = () => api.getMonitorEvents(selectedService).then((data) => setMonitorEvents(data.events || []) ).catch(() => undefined);
    load(); const timer = window.setInterval(load, LIVE_POLL_INTERVAL_MS); return () => window.clearInterval(timer);
  }, [selectedService]);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);

  const runNormalRequest = async () => {
    setAction("normal");
    setError("");
    try {
      await api.triggerHop();
      await Promise.all([reloadData(), refreshLatestTrace()]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Normal request failed");
    } finally {
      setAction(null);
    }
  };

  const injectLiveFault = async (service: string, delayMs: number, requestCount: number, dispatchNow: boolean) => {
    setAction("fault");
    setError("");
    try {
      await api.armFault(service, Math.min(60000, Math.max(1, delayMs || 1)), Math.min(25, Math.max(1, requestCount || 1)));
      if (dispatchNow) await api.triggerHop();
      await Promise.all([reloadData(), refreshLatestTrace()]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Live fault injection failed");
    } finally {
      setAction(null);
    }
  };

  const selectedConfig = health[selectedService]?.monitor_config;
  const saveConfig = async (config: any) => { setSavingConfig(true); try { await api.saveMonitorConfig(selectedService, config); await reloadData(); } finally { setSavingConfig(false); } };
  const setOverride = async (state: "auto" | "normal" | "error") => { await api.setOverride(selectedService, state); await reloadData(); };

  return (
    <main className="container">
      <div className="page-heading">
        <div><p className="eyebrow">Live causal observability</p><h1>System dashboard</h1><p>Per-service anomaly detection, real request state, and persisted saga compensation.</p></div>
        {error && <div className="error-banner">{error}</div>}
      </div>
      <TopStatusBar health={health} now={now} />
      <div className="dashboard-with-sidebar">
        <ServiceSidebar services={chain} health={health} selected={selectedService} onSelect={setSelectedService} />
        <div className="dashboard-content">
          <div className="service-detail-heading"><div><span className="eyebrow">Selected service</span><h2>{selectedService || "Loading services…"}</h2></div><div className="override-controls"><span>Demo override</span><button className="btn btn-sm" onClick={() => setOverride("normal")}>Simulate normal</button><button className="btn btn-rose btn-sm" onClick={() => setOverride("error")}>Simulate error</button><button className="btn btn-sm" onClick={() => setOverride("auto")}>Auto</button></div></div>
          <CombinedMetricChart service={selectedService} points={anomalies.filter((point) => point.service === selectedService)} events={monitorEvents} rollbacks={liveTrace?.rollbacks || []} />
          <ServiceConfigPanel service={selectedService} config={selectedConfig} onSave={saveConfig} saving={savingConfig} />
          <div className="grid-main">
            <div>
          <MicroservicesPanel services={chain} onNormalRequest={runNormalRequest} onLiveFault={injectLiveFault} action={action} />
          <section className="card live-graph-card">
            <div className="card-title"><span>Latest causal graph</span><span className="mono trace-caption">{liveTrace?.trace_id || "awaiting trace"}</span></div>
            <CausalGraph nodes={liveTrace?.nodes || []} edges={liveTrace?.edges || []} live liveStates={health} />
          </section>
          <section className="card panel-spaced traces-card">
            <div className="card-title">Recent request traces</div>
            <div className="table-container"><table className="table"><thead><tr><th>Trace</th><th>Initiator</th><th>Spans</th><th>Type</th><th /></tr></thead><tbody>
              {traces.map((trace) => <tr key={trace.trace_id}><td className="mono cyan-text">{trace.trace_id}</td><td>{trace.service}</td><td>{trace.span_count}</td><td><span className={`badge ${trace.is_fault_run ? "badge-down" : "badge-up"}`}>{trace.is_fault_run ? "fault observed" : "normal"}</span></td><td><Link to={`/trace/${trace.trace_id}`} className="btn btn-sm">Inspect</Link></td></tr>)}
            </tbody></table></div>
          </section>
            </div>
            <IncidentFeed incidents={incidents} />
          </div>
        </div>
      </div>
    </main>
  );
};

const TraceDetailView: React.FC = () => {
  const { traceId } = useParams<{ traceId: string }>();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [currentStep, setCurrentStep] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  useEffect(() => {
    if (!traceId) return undefined;
    let mounted = true;
    const loadTrace = async () => {
      const result = await api.getTrace(traceId);
      if (!mounted) return;
      setData(result);
      setLoading(false);
      setCurrentStep((step) => {
        const latestMaxStep = Math.max((result.nodes?.length || 1) - 1, 0);
        if (isFirstPayload) {
          isFirstPayload = false;
          return latestMaxStep;
        }
        return Math.min(step, latestMaxStep);
      });
    };
    let isFirstPayload = true;
    loadTrace();
    const interval = window.setInterval(loadTrace, LIVE_POLL_INTERVAL_MS);
    return () => { mounted = false; window.clearInterval(interval); };
  }, [traceId]);

  const nodes = data?.nodes || [];
  const maxSteps = Math.max(nodes.length - 1, 0);
  useEffect(() => {
    if (!isPlaying) return undefined;
    const timer = window.setInterval(() => setCurrentStep((step) => {
      if (step >= maxSteps) { setIsPlaying(false); return maxSteps; }
      return step + 1;
    }), 900);
    return () => window.clearInterval(timer);
  }, [isPlaying, maxSteps]);

  if (loading || !data) return <main className="container"><div className="card loading-card">Loading live causal graph…</div></main>;
  const rollbacks = data.rollbacks || data.rollback_events || [];

  return (
    <main className="container trace-page">
      <Link to="/" className="btn btn-sm back-link">← Back to dashboard</Link>
      <div className="trace-heading"><div><p className="eyebrow">Live trace replay</p><h1 className="mono">{traceId}</h1></div><span className="live-label"><i /> Syncing persisted state</span></div>
      <section className={`card diagnosis-banner ${data.root_cause_service ? "has-root" : "healthy"}`}>
        <div className="diagnosis-badges"><span className={`badge ${data.root_cause_service ? "badge-root" : "badge-up"}`}>{data.root_cause_service ? `Root cause: ${data.root_cause_service}` : "healthy trace"}</span>{data.symptom_service && <span className="badge badge-symptom">Symptom: {data.symptom_service}</span>}</div>
        <p>{data.explanation}</p>
      </section>
      <section className="card panel-spaced">
        <div className="card-title"><span>Interactive time-travel replay</span><span className="live-label"><i /> Database-backed rollback markers</span></div>
        <div className="scrubber-panel"><button className="btn btn-sm btn-cyan" onClick={() => setIsPlaying(!isPlaying)}>{isPlaying ? "Pause" : "Play"}</button><button className="btn btn-sm" onClick={() => setCurrentStep(Math.max(0, currentStep - 1))}>Step ←</button><button className="btn btn-sm" onClick={() => setCurrentStep(Math.min(maxSteps, currentStep + 1))}>Step →</button><input type="range" min="0" max={maxSteps} value={currentStep} onChange={(event) => setCurrentStep(Number(event.target.value))} className="slider" /><span className="mono replay-position">Hop {currentStep + 1}/{maxSteps + 1}</span></div>
        <CausalGraph nodes={nodes} edges={data.edges || []} currentStep={currentStep} />
      </section>
      {(data.state_history || []).length > 0 && <RollbackPlayback events={data.state_history} />}
      {rollbacks.length > 0 && <section className="card panel-spaced rollback-card"><div className="card-title"><span>Persisted saga rollbacks</span><span className="mono">{rollbacks.length} database rows</span></div><div className="rollback-list">{rollbacks.map((rollback: any) => <div key={rollback.id || rollback.span_id} className={`rollback-row ${rollback.status}`}><div><strong>↶ {rollback.service}</strong><p>{rollback.reason}</p></div><div><span className={`badge ${rollback.status === "completed" ? "badge-up" : "badge-root"}`}>{rollback.status}</span><small className="mono">by {rollback.triggered_by_service}</small></div></div>)}</div></section>}
      <section className="card panel-spaced"><div className="card-title">Hop metrics · self-time isolation</div><div className="table-container"><table className="table"><thead><tr><th>#</th><th>Service</th><th>Total</th><th>Self time</th><th>Baseline</th><th>Stdev</th><th>Z</th><th>State</th></tr></thead><tbody>{nodes.map((node: any, index: number) => { const role = node.is_root_cause ? "root cause" : node.is_symptom ? "symptom" : node.is_cascade ? "cascade" : "normal"; return <tr key={node.span_id} className={node.is_root_cause ? "highlight-rc" : ""}><td className="mono">{index + 1}</td><td>{node.service}</td><td className="mono">{node.duration_ms}ms</td><td className="mono cyan-text">{node.self_time_ms}ms</td><td className="mono">{node.baseline_mean}ms</td><td className="mono">{node.baseline_stdev}ms</td><td className={`mono z-score ${node.z_score > 2 ? "high" : ""}`}>{node.z_score?.toFixed(2)}</td><td><span className={`badge ${node.rollback_status ? "badge-root" : node.is_root_cause ? "badge-root" : node.is_symptom ? "badge-symptom" : "badge-up"}`}>{node.rollback_status ? `rollback ${node.rollback_status}` : role}</span></td></tr>; })}</tbody></table></div></section>
    </main>
  );
};

const App: React.FC = () => <Router><Navbar /><Routes><Route path="/" element={<DashboardView />} /><Route path="/trace/:traceId" element={<TraceDetailView />} /></Routes></Router>;

export default App;
