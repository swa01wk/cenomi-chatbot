interface SceneSnapshotCardProps {
  scene: Record<string, unknown>;
}

export default function SceneSnapshotCard({ scene }: SceneSnapshotCardProps) {
  const entries = Object.entries(scene).filter(
    ([, v]) =>
      v !== "" &&
      v !== null &&
      v !== undefined &&
      !(Array.isArray(v) && v.length === 0),
  );

  if (entries.length === 0) {
    return (
      <p className="text-xs italic text-slate-500">No scene data captured</p>
    );
  }

  return (
    <div className="space-y-1.5">
      {entries.map(([key, value]) => (
        <div key={key} className="flex items-start gap-2 text-xs">
          <span className="shrink-0 font-medium text-slate-400">
            {key.replace(/_/g, " ")}
          </span>
          <span className="text-slate-200">
            {Array.isArray(value) ? value.join(", ") : String(value)}
          </span>
        </div>
      ))}
    </div>
  );
}
