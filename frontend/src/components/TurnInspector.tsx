import {
  Brain,
  Compass,
  Clock,
  AlertTriangle,
} from "lucide-react";
import SceneSnapshotCard from "./SceneSnapshotCard";
import StrategyCard from "./StrategyCard";
import ContextBlocksCard from "./ContextBlocksCard";
import RetrievalCard from "./RetrievalCard";
import type { DebugPayload } from "../types/chat";

interface TurnInspectorProps {
  debug: DebugPayload;
}

export default function TurnInspector({ debug }: TurnInspectorProps) {
  return (
    <div className="space-y-1">
      {/* Intent */}
      <Section title="Intent" icon={<Brain size={13} className="text-amber-400" />}>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          <Field label="Domain" value={debug.intent_domain} />
          <Field label="Sub-intent" value={debug.intent_sub} />
          <Field label="Kind" value={debug.message_kind.replace(/_/g, " ")} />
          <Field
            label="Confidence"
            value={`${Math.round(debug.intent_confidence * 100)}%`}
          />
        </div>
      </Section>

      {/* Scene */}
      <Section
        title="Scene Snapshot"
        icon={<Compass size={13} className="text-teal-400" />}
      >
        <SceneSnapshotCard scene={debug.scene_summary} />
      </Section>

      {/* Playbook & Strategy */}
      <Section title="Playbook & Strategy">
        <StrategyCard
          selectedPlaybook={debug.selected_playbook}
          playbookConfidence={debug.playbook_confidence}
          matchedPlaybooks={debug.matched_playbooks}
          chosenStrategy={debug.chosen_strategy}
          responseShape={debug.response_shape}
        />
      </Section>

      {/* Context Composition */}
      <Section title="Context">
        <ContextBlocksCard
          topicBlocks={debug.selected_topic_blocks}
          entities={debug.selected_entities}
          semanticSignals={debug.selected_semantic_signals}
          rankingNotes={debug.ranking_notes}
        />
      </Section>

      {/* Retrieval */}
      <Section title="Retrieval">
        <RetrievalCard
          needed={debug.retrieval_needed}
          reason={debug.retrieval_reason}
          targets={debug.retrieval_targets}
          resultsCount={debug.retrieval_results_count}
        />
      </Section>

      {/* Performance */}
      <Section
        title="Performance"
        icon={<Clock size={13} className="text-orange-400" />}
      >
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-400">Total latency</span>
            <span className="font-mono font-medium text-slate-200">
              {debug.total_latency_ms.toFixed(0)}ms
            </span>
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-400">Nodes executed</span>
            <span className="font-mono text-slate-300">{debug.node_count}</span>
          </div>
          <div className="space-y-1">
            {Object.entries(debug.latency_by_node)
              .sort(([, a], [, b]) => b - a)
              .map(([node, ms]) => {
                const pct =
                  debug.total_latency_ms > 0
                    ? (ms / debug.total_latency_ms) * 100
                    : 0;
                return (
                  <div key={node} className="space-y-0.5">
                    <div className="flex items-center justify-between text-[10px]">
                      <span className="text-slate-400">
                        {node.replace(/_/g, " ")}
                      </span>
                      <span className="font-mono text-slate-500">
                        {ms.toFixed(0)}ms
                      </span>
                    </div>
                    <div className="h-1 overflow-hidden rounded-full bg-slate-700">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-blue-500 to-blue-400"
                        style={{ width: `${Math.max(pct, 2)}%` }}
                      />
                    </div>
                  </div>
                );
              })}
          </div>
        </div>
      </Section>

      {/* Warnings */}
      {debug.warnings.length > 0 && (
        <Section
          title="Warnings"
          icon={<AlertTriangle size={13} className="text-yellow-400" />}
        >
          <div className="space-y-1">
            {debug.warnings.map((w, i) => (
              <p
                key={i}
                className="text-[11px] leading-relaxed text-yellow-300/80"
              >
                {w}
              </p>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg bg-slate-800/60 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
        {icon}
        {title}
      </div>
      {children}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-slate-500">{label}: </span>
      <span className="font-medium text-slate-200">{value || "—"}</span>
    </div>
  );
}
