"use client";

import type { ComparatorOption } from "@/lib/api";

const groups: ComparatorOption["group"][] = [
  "Cluster members",
  "Partial overlaps",
  "Other slice diseases",
];

export function ComparatorSelect({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: ComparatorOption[];
  value: string;
  onChange: (value: string) => void;
}) {
  return <label className="comparator-control">{label}
    <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      {groups.map((group) => {
        const rows = options.filter((option) => option.group === group);
        return rows.length ? <optgroup key={group} label={group}>{rows.map(({ disease }) =>
          <option key={disease.id} value={disease.id}>{disease.gene_symbol} · {disease.name}</option>,
        )}</optgroup> : null;
      })}
    </select>
  </label>;
}
