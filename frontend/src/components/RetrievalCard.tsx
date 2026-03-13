import { Search, CheckCircle, XCircle } from "lucide-react";

interface RetrievalCardProps {
  needed: boolean;
  reason: string;
  targets: string[];
  resultsCount: number;
}

export default function RetrievalCard({
  needed,
  reason,
  targets,
  resultsCount,
}: RetrievalCardProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Search size={13} className="text-blue-400" />
        {needed ? (
          <div className="flex items-center gap-1.5">
            <CheckCircle size={12} className="text-green-400" />
            <span className="text-xs text-green-400">Retrieval triggered</span>
            <span className="rounded bg-slate-700 px-1.5 py-0.5 text-[10px] text-slate-300">
              {resultsCount} result{resultsCount !== 1 ? "s" : ""}
            </span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5">
            <XCircle size={12} className="text-slate-500" />
            <span className="text-xs text-slate-400">Skipped</span>
          </div>
        )}
      </div>

      {reason && (
        <p className="text-[11px] leading-relaxed text-slate-400">{reason}</p>
      )}

      {targets.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {targets.map((t) => (
            <span
              key={t}
              className="rounded bg-blue-900/30 px-1.5 py-0.5 text-[10px] text-blue-300"
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
