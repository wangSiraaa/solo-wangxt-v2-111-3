export interface AssayVersion {
  id: number;
  version: string;
  lab_report_no: string;
  assayed_at: string;
  basis: 'dry' | 'wet';
  composition: Record<string, number>;
  measured_oxides: string[];
}

export interface Material {
  id: number;
  code: string;
  name: string;
  category: string;
  moisture_pct: number;
  cost_per_t_wet: number;
  availability_t_wet: number | null;
  min_share_pct: number;
  is_active: boolean;
  note?: string | null;
  assay_versions: AssayVersion[];
}

export interface Interval { min?: number | null; max?: number | null; }
export interface Targets { SM: Interval; IM: Interval; KH: Interval; }

export interface SpecSnapshot {
  targets: Targets;
  hazard_limits_pct: Record<string, number>;
}

export type SpecRevisionStatus = 'draft' | 'published' | 'superseded' | 'deprecated';

export interface SpecRevision {
  id: number;
  family_id: number;
  spec_code: string;
  spec_name: string;
  revision_no: string;
  status: SpecRevisionStatus;
  spec_snapshot: SpecSnapshot;
  lock_version: number;
  created_at: string;
  published_at?: string | null;
  superseded_at?: string | null;
  created_from_revision_id?: number | null;
  change_note?: string | null;
}

export interface SpecFamily {
  id: number;
  spec_code: string;
  name: string;
  status: 'active' | 'deprecated';
  lock_version: number;
  note?: string | null;
  created_at: string;
  deprecated_at?: string | null;
  current_revision_id: number | null;
  current_revision_no: string | null;
  draft_revision_id: number | null;
  revisions: SpecRevision[];
}

export interface SpecBinding {
  revision_id: number;
  spec_code: string;
  spec_name?: string | null;
  revision_no: string;
  status: SpecRevisionStatus;
  snapshot: SpecSnapshot;
}

export interface SpecDiffChange { path: string; from: number | null; to: number | null; }
export interface SpecDiff {
  from_revision: SpecRevision;
  to_revision: SpecRevision;
  changes: SpecDiffChange[];
}

export interface BlendRequest {
  scenario_name: string;
  batch_t_dry: number;
  candidates: { material_id: number; assay_version_id?: number | null }[];
  spec_revision_id?: number | null;
  targets?: Targets | null;
  hazard_limits_pct?: Record<string, number> | null;
  modes: string[];
  cheap_material_id?: number | null;
  save?: boolean;
}

export interface ConversionStep {
  component: string;
  basis_in: string;
  value_in: number;
  formula: string;
  factor: number;
  basis_out: string;
  value_out: number;
}

export interface SolutionItem {
  material_code: string;
  material_name: string;
  assay_version: string;
  lab_report_no: string;
  share_pct_dry: number;
  mass_t_dry: number;
  mass_t_wet: number;
  water_t: number;
  cost: number;
  conversion_trace: {
    material_code: string;
    material_name: string;
    moisture_pct: number;
    assay_basis: string;
    dry_factor: number;
    steps: ConversionStep[];
    mass_balance?: any;
  };
}

export interface Conflict {
  constraint: string;
  limit?: number;
  achieved?: number;
  normalized_gap: number;
}

export interface Solution {
  mode: string;
  mode_label: string;
  success: boolean;
  total_cost?: number;
  cost_per_t_dry?: number;
  indicators?: {
    SM: number; IM: number; KH: number;
    CaO: number; SiO2: number; Al2O3: number; Fe2O3: number;
    warnings: string[];
  };
  composition_dry_pct?: Record<string, number>;
  composition_wet_pct?: Record<string, number>;
  water_pct_in_wet_mix?: number;
  items: SolutionItem[];
  diagnostic?: {
    reason: string; message: string;
    conflicts: Conflict[];
    min_violation_objective?: number;
  };
}

export interface BlendResponse {
  run_id: number | null;
  run_code: string;
  status: string;
  spec?: SpecBinding | null;
  solutions: Solution[];
}

export interface RunSummary {
  id: number; run_code: string; scenario_name: string;
  status: string; created_at: string; modes: string[];
  spec_revision_id?: number | null;
  spec_code?: string | null;
  spec_revision_no?: string | null;
}

export interface RunDetail {
  id: number; run_code: string; scenario_name: string;
  batch_t_dry: number; target: Targets; constraint_set: any;
  status: string; created_at: string;
  spec?: {
    revision_id: number; spec_code: string; revision_no: string;
    snapshot: SpecSnapshot;
  } | null;
  solutions: any[];
}
