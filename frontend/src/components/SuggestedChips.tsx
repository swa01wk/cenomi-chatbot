interface SuggestedChipsProps {
  chips: string[];
  onSelect: (chip: string) => void;
  disabled?: boolean;
}

export default function SuggestedChips({
  chips,
  onSelect,
  disabled,
}: SuggestedChipsProps) {
  if (chips.length === 0) return null;

  return (
    <div className="animate-fade-in flex flex-wrap gap-2">
      {chips.map((chip) => (
        <button
          key={chip}
          onClick={() => onSelect(chip)}
          disabled={disabled}
          className="rounded-full border border-gray-200 bg-white px-3.5 py-1.5 text-sm text-gray-600 shadow-sm transition hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 active:scale-95 disabled:opacity-40"
        >
          {chip}
        </button>
      ))}
    </div>
  );
}
