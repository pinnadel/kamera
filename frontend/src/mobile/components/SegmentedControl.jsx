// SegmentedControl — iOS-style segmented picker. Used for Bursts/People in
// GroupView and other binary/triadic toggles. Real radiogroup semantics.

export function SegmentedControl({ value, onChange, options, label }) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex p-0.5 rounded-full bg-[#101111] border border-white/5"
    >
      {options.map(opt => {
        const selected = opt.value === value
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(opt.value)}
            className={`px-3 h-9 rounded-full text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4] ${
              selected
                ? 'bg-[#252628] text-[#f9f9f9] shadow-[inset_0_0_0_1px_rgba(91,184,212,0.30)]'
                : 'text-[#9c9c9d] hover:text-[#cecece]'
            }`}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
