import {Download, ExternalLink} from 'lucide-react';

const graphs = [
  ['graph_01_average_latency.png', 'Average interactive latency', 'Mean modeled latency with standard deviation across five paired seeds.'],
  ['graph_02_p95_latency.png', '95th percentile latency', 'Per-tick modeled latency distribution summary.'],
  ['graph_03_jitter.png', 'Jitter', 'Tick-to-tick change in modeled queue delay; not packet-level jitter.'],
  ['graph_04_packet_loss.png', 'Packet loss', 'Modeled dropped packets as a share of offered packets.'],
  ['graph_05_class_throughput.png', 'Throughput by traffic class', 'Average scheduled throughput for each generated traffic class.'],
  ['graph_06_capacity_drop_response.png', 'Capacity reduction response', 'Latency and queue response to a configured link capacity drop.'],
  ['graph_07_queue_depth.png', 'Queue depth', 'Average number of modeled packets waiting in queues.'],
  ['graph_08_fairness_and_minimum_service.png', 'Fairness and minimum service', 'Jain fairness index and minimum class service across scenarios.'],
  ['graph_09_adaptation_stability.png', 'Allocation response and stability', 'Per-class allocation changes and modeled allocation churn.'],
  ['graph_10_cpu_overhead.png', 'Python model CPU time', 'Local process CPU time per simulation tick; not gateway CPU.'],
  ['graph_11_classifier_confusion_matrix.png', 'Generated-label classifier check', 'Metadata classifier compared with generated scenario labels.'],
  ['graph_12_capacity_estimation.png', 'Capacity estimator check', 'Estimator output from seeded simulated probes at known rates.'],
  ['graph_13_temporary_intent.png', 'Temporary service intent', 'Model behavior before, during, and after a one-minute intent.'],
] as const;

const files = [
  ['summary.csv', 'Summary table (CSV)'],
  ['raw_timeseries.csv', 'Per-tick results (CSV)'],
  ['raw_trials.json', 'Full paired trials (JSON)'],
  ['classifier_report.json', 'Classifier report (JSON)'],
  ['capacity_estimation.csv', 'Capacity estimator samples (CSV)'],
  ['temporary_intent.json', 'Temporary intent validation (JSON)'],
  ['packet_lab_status.json', 'Packet lab status (JSON)'],
] as const;

export default function ExperimentalEvidence() {
  return <>
    <div className="page-heading simple-heading">
      <div><div className="eyebrow">PUBLISHED REPORT <span className="eyebrow-line"/></div><h1>Experimental evidence</h1><p>Shared, static report files included with this deployment.</p></div>
    </div>
    <div className="evidence-notice panel"><b>Model results, not home-router measurements</b><p>These results come from the project's synthetic traffic model. They do not measure a visitor's Wi-Fi, ISP, or router. The packet lab status is reported separately.</p></div>
    <div className="evidence-stats">
      <div className="panel"><small>PAIRED POLICY RUNS</small><b>60</b><span>4 scenarios × 3 policies × 5 seeds</span></div>
      <div className="panel"><small>MODEL TICKS</small><b>40</b><span>One simulated second per tick</span></div>
      <div className="panel"><small>POLICIES COMPARED</small><b>3</b><span>No QoS · Static QoS · Adaptive QoS</span></div>
    </div>
    <section className="panel evidence-findings">
      <div className="panel-head"><div><div className="panel-title">What these runs show</div><div className="panel-sub">Mean outcomes from the generated traffic scenarios</div></div><span className="source-chip"><i/> SIMULATED DATA</span></div>
      <ul>
        <li>In the work-call workload, modeled latency averaged 60.8 ms with No QoS and 26.9 ms with both Static and Adaptive QoS.</li>
        <li>During the capacity drop, Static and Adaptive QoS lowered modeled latency versus No QoS, while jitter and queue depth were higher.</li>
        <li>Static and Adaptive QoS had the same core metric means in these runs, so this evidence does not show an added benefit from the adaptive policy.</li>
      </ul>
    </section>
    <div className="evidence-toolbar"><div><b>Performance graphs</b><span>Open a graph at full size.</span></div><div className="evidence-downloads">{files.map(([file,label])=><a key={file} href={`/evidence/${file}`} download><Download size={13}/>{label}</a>)}</div></div>
    <div className="evidence-gallery">{graphs.map(([file,title,description])=><article className="panel evidence-card" key={file}><div className="evidence-card-copy"><b>{title}</b><p>{description}</p></div><a href={`/evidence/${file}`} target="_blank" rel="noreferrer" aria-label={`Open ${title} full size`}><img src={`/evidence/${file}`} alt={title} loading="lazy"/><span><ExternalLink size={13}/> Open full size</span></a></article>)}</div>
    <p className="evidence-footer-note">This report is versioned with the frontend deployment. Visitors to the same deployment see the same files; running or saving a new experiment does not change this published report.</p>
  </>;
}
