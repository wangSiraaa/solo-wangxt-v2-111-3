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

export interface BlendRequest {
  scenario_name: string;
  batch_t_dry: number;
  candidates: { material_id: number; assay_version_id?: number | null }[];
  targets: Targets;
  hazard_limits_pct: Record<string, number>;
  modes: string[];
  cheap_material_id?: number | null;
  save?: boolean;
  spec_revision_id?: number | null;
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
  solutions: Solution[];
  spec?: SpecBinding | null;
}

export interface SpecBinding {
  spec_id: number;
  spec_code: string;
  spec_name: string;
  revision_id: number;
  revision_no: number;
  targets: Targets;
  hazard_limits_pct: Record<string, number>;
}

export interface SpecRevision {
  id: number;
  revision_no: number;
  targets: Targets;
  hazard_limits_pct: Record<string, number>;
  published_at: string;
  note?: string | null;
}

export type SpecStatus = 'draft' | 'published' | 'retired';

export interface ConstraintSpec {
  id: number;
  code: string;
  name: string;
  status: SpecStatus;
  lock_version: number;
  draft_targets?: Targets | null;
  draft_hazard_limits_pct?: Record<string, number> | null;
  note?: string | null;
  created_at: string;
  revisions: SpecRevision[];
}

export interface SpecDiffChange {
  field: string;
  from: number | null;
  to: number | null;
  direction: '新增' | '删除' | '收紧' | '放宽';
}

export interface SpecDiff {
  spec_id: number;
  spec_code: string;
  spec_name: string;
  from_revision: number;
  to_revision: number;
  changes: SpecDiffChange[];
}

export interface SpecSnapshot extends SpecBinding {
  published_at: string;
}

export interface RunSummary {
  id: number; run_code: string; scenario_name: string;
  status: string; created_at: string; modes: string[];
  spec?: { spec_id: number; spec_code: string; revision_no: number } | null;
}

export interface RunDetail {
  id: number; run_code: string; scenario_name: string;
  batch_t_dry: number; target: Targets; constraint_set: any;
  status: string; created_at: string;
  spec_snapshot?: SpecSnapshot | null;
  solutions: any[];
}
