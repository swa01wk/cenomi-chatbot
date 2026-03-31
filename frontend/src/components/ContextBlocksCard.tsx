import { Layers, Tag, Radio } from "lucide-react";

interface ContextBlocksCardProps {
  topicBlocks: string[];
  entities: Array<Record<string, unknown>>;
  semanticSignals: string[];
  rankingNotes: string[];
}

export default function ContextBlocksCard({
  topicBlocks,
  entities,
  semanticSignals,
  rankingNotes,
}: ContextBlocksCardProps) {
  return (
    <div className="space-y-3">
      {topicBlocks.length > 0 && (
        <Row
          icon={<Layers size={12} className="text-cyan-400" />}
          label="Topic blocks"
        >
          <div className="flex flex-wrap gap-1">
            {topicBlocks.map((b) => (
              <span
                key={b}
                className="rounded bg-cyan-900/30 px-1.5 py-0.5 text-[10px] text-cyan-300"
              >
                {b}
              </span>
            ))}
          </div>
        </Row>
      )}

      {entities.length > 0 && (
        <Row
          icon={<Tag size={12} className="text-emerald-400" />}
          label="Entities"
        >
          <div className="space-y-1">
            {entities.map((e, i) => (
              <div key={i} className="flex flex-wrap gap-1.5 text-[11px]">
                <span className="font-medium text-slate-200">
                  {String(e.name || "?")}
                </span>
                {Object.entries(e)
                  .filter(([k]) => k !== "name")
                  .map(([k, v]) => (
                    <span key={k} className="text-slate-500">
                      {k}:{" "}
                      {typeof v === "object" && v !== null
                        ? JSON.stringify(v)
                        : String(v)}
                    </span>
                  ))}
              </div>
            ))}
          </div>
        </Row>
      )}

      {semanticSignals.length > 0 && (
        <Row
          icon={<Radio size={12} className="text-violet-400" />}
          label="Signals"
        >
          <div className="flex flex-wrap gap-1">
            {semanticSignals.map((s) => (
              <span
                key={s}
                className="rounded bg-violet-900/30 px-1.5 py-0.5 text-[10px] text-violet-300"
              >
                {s}
              </span>
            ))}
          </div>
        </Row>
      )}

      {rankingNotes.length > 0 && (
        <div className="border-t border-slate-700 pt-2">
          {rankingNotes.map((n, i) => (
            <p key={i} className="text-[11px] leading-relaxed text-slate-500">
              {n}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function Row({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
        {icon}
        {label}
      </div>
      {children}
    </div>
  );
}
