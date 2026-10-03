import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  ApiService,
} from '../services/api.service';
import {
  BlendResponse, ConstraintSpec, Material, Solution, Targets,
} from '../models/models';
import { SolutionCardComponent } from './solution-card.component';

interface CandRow {
  material_id: number;
  assay_version_id: number | null;
  selected: boolean;
}

interface Preset {
  key: string;
  label: string;
  desc: string;
  ids: number[];
  targets: Targets;
  hazards: Record<string, number>;
  modes: string[];
  cheap_id?: number;
  batch?: number;
  demo?: boolean;
}

/** 已发布规范修订版的下拉选项。 */
interface SpecOption {
  revision_id: number;
  label: string;
  targets: Targets;
  hazards: Record<string, number>;
}

const BASE_T: Targets = { SM: { min: 2.4, max: 2.8 }, IM: { min: 1.4, max: 1.8 }, KH: { min: 0.88, max: 0.94 } };

@Component({
  selector: 'app-blend',
  standalone: true,
  imports: [CommonModule, FormsModule, SolutionCardComponent],
  templateUrl: './blend.component.html',
})
export class BlendComponent implements OnInit {
  materials: Material[] = [];
  cand: Record<number, CandRow> = {};
  targets: Targets = JSON.parse(JSON.stringify(BASE_T));
  hazardCl = 0.05;
  hazardAlkali = 1.5;
  batch = 1000;
  modes = { min_cost: true, max_cheap: true, balanced: true };
  cheapId: number | null = 4;
  scenario = '含水率差异 + 多方案对比（虚构边界）';
  loading = false;
  result: BlendResponse | null = null;
  apiError: any = null;

  // 约束来源：null = 临时参数；否则为已发布规范修订版 id
  specOptions: SpecOption[] = [];
  selectedRevisionId: number | null = null;

  // 手工配比（错误演示）
  evalIds = [8];
  evalShares = [100];
  evalResult: any = null;
  evalError: any = null;
  evalBusy = false;

  presets: Preset[] = [
    {
      key: 'base', label: '基准：三方案对比',
      desc: '5 种常规原料，成本最优 / 粉煤灰用量最大 / 率值居中，对比含水率对湿料采购量的影响。',
      ids: [1, 2, 3, 4, 5], targets: JSON.parse(JSON.stringify(BASE_T)),
      hazards: { Cl: 0.05, alkali_eq: 1.5 },
      modes: ['min_cost', 'max_cheap', 'balanced'], cheap_id: 4,
    },
    {
      key: 'cheap', label: '廉价原料致指标超限',
      desc: '仅石灰石+高碱页岩且无铁质校正：廉价料拉低成本但 IM/KH 超限，展示失败与冲突项。',
      ids: [1, 3], targets: JSON.parse(JSON.stringify(BASE_T)),
      hazards: { Cl: 0.05, alkali_eq: 1.5 },
      modes: ['min_cost'],
    },
    {
      key: 'alkali', label: '有害组分（碱当量）上限冲突',
      desc: '碱当量收紧到 0.40%（干基），廉价页岩/粉煤灰无法同时满足，诊断指明突破量。',
      ids: [1, 2, 3, 4, 5], targets: JSON.parse(JSON.stringify(BASE_T)),
      hazards: { Cl: 0.05, alkali_eq: 0.4 },
      modes: ['min_cost'],
    },
    {
      key: 'avail', label: '大批量可用量约束',
      desc: '5000 t 干生料超过石灰石 3000 t 湿基可用量，KH 与可用量同时冲突。',
      ids: [1, 2, 3, 4, 5], targets: JSON.parse(JSON.stringify(BASE_T)),
      hazards: { Cl: 0.05, alkali_eq: 1.5 },
      modes: ['min_cost'], batch: 5000,
    },
  ];

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.api.materials(false).subscribe(ms => {
      this.materials = ms;
      for (const m of ms) {
        this.cand[m.id] = {
          material_id: m.id,
          assay_version_id: m.assay_versions[0]?.id ?? null,
          selected: [1, 2, 3, 4, 5].includes(m.id),
        };
      }
    });
    this.reloadSpecs();
  }

  reloadSpecs(): void {
    this.api.specs().subscribe(ss => {
      this.specOptions = [];
      for (const s of ss) {
        if (s.status === 'retired') continue;  // 停用规范禁止新试算引用
        for (const r of s.revisions) {
          this.specOptions.push({
            revision_id: r.id,
            label: `${s.code} ${s.name} · r${r.revision_no}`,
            targets: r.targets,
            hazards: r.hazard_limits_pct,
          });
        }
      }
      // 已选修订版若不再可引用（如规范被停用），回退到临时参数
      if (this.selectedRevisionId != null
          && !this.specOptions.some(o => o.revision_id === this.selectedRevisionId)) {
        this.selectedRevisionId = null;
      }
      this.applySpecParams();
    });
  }

  get usingSpec(): boolean { return this.selectedRevisionId != null; }

  selectedSpecOption(): SpecOption | undefined {
    return this.specOptions.find(o => o.revision_id === this.selectedRevisionId);
  }

  /** 选中规范后，率值窗口/有害限值切换为冻结值且只读。 */
  onSpecChange(): void {
    this.applySpecParams();
    this.result = null;
  }

  private applySpecParams(): void {
    const opt = this.selectedSpecOption();
    if (!opt) return;
    this.targets = JSON.parse(JSON.stringify(opt.targets));
    this.hazardCl = opt.hazards['Cl'] ?? this.hazardCl;
    this.hazardAlkali = opt.hazards['alkali_eq'] ?? this.hazardAlkali;
  }

  mat(id: number): Material | undefined { return this.materials.find(m => m.id === id); }

  chosenVersions(id: number) { return this.mat(id)?.assay_versions ?? []; }

  applyPreset(p: Preset): void {
    this.scenario = p.label;
    this.selectedRevisionId = null;  // 快速场景使用临时参数
    for (const m of this.materials) {
      this.cand[m.id].selected = p.ids.includes(m.id);
    }
    this.targets = JSON.parse(JSON.stringify(p.targets));
    this.hazardCl = p.hazards['Cl'] ?? 0.05;
    this.hazardAlkali = p.hazards['alkali_eq'] ?? 1.5;
    this.batch = p.batch ?? 1000;
    this.modes = {
      min_cost: p.modes.includes('min_cost'),
      max_cheap: p.modes.includes('max_cheap'),
      balanced: p.modes.includes('balanced'),
    };
    if (p.cheap_id) this.cheapId = p.cheap_id;
  }

  selectedCandidates() {
    return Object.values(this.cand).filter(c => c.selected);
  }

  selectedModes(): string[] {
    return (['min_cost', 'max_cheap', 'balanced'] as const)
      .filter(k => this.modes[k]);
  }

  solve(): void {
    this.apiError = null;
    const cands = this.selectedCandidates();
    if (!cands.length) { this.apiError = { message: '请至少勾选一种候选原料。' }; return; }
    if (!this.selectedModes().length) { this.apiError = { message: '请至少选择一种求解模式。' }; return; }
    this.loading = true;
    this.result = null;
    this.api.blend({
      scenario_name: this.scenario,
      batch_t_dry: this.batch,
      candidates: cands.map(c => ({
        material_id: c.material_id,
        assay_version_id: c.assay_version_id,
      })),
      targets: this.targets,
      hazard_limits_pct: { Cl: this.hazardCl, alkali_eq: this.hazardAlkali },
      modes: this.selectedModes(),
      cheap_material_id: this.modes.max_cheap ? this.cheapId : null,
      save: true,
      spec_revision_id: this.selectedRevisionId,
    }).subscribe({
      next: r => { this.result = r; this.loading = false; },
      error: e => {
        this.apiError = e.error ?? { message: '请求失败：' + e.message };
        this.loading = false;
        // 规范被并发停用/改版时刷新可选项，便于用户处理后重试
        if (['SPEC_RETIRED', 'SPEC_CONFLICT'].includes(this.apiError?.error_code)) {
          this.reloadSpecs();
        }
      },
    });
  }

  // ---------- 手工配比错误演示 ----------
  setEvalDemo(kind: 'zero' | 'missing'): void {
    if (kind === 'zero') { this.evalIds = [8]; this.evalShares = [100]; }
    else { this.evalIds = [7, 1]; this.evalShares = [20, 80]; }
    this.evalResult = null; this.evalError = null;
  }

  onEvalIdsChange(): void {
    this.evalShares = this.evalIds.map(() => Math.round(100 / this.evalIds.length));
    this.evalResult = null; this.evalError = null;
  }

  evaluate(): void {
    this.evalError = null; this.evalResult = null; this.evalBusy = true;
    this.api.evaluate(
      this.evalIds.map(id => ({ material_id: id })),
      this.evalShares,
      '手工配比错误演示',
    ).subscribe({
      next: r => { this.evalResult = r; this.evalBusy = false; },
      error: e => { this.evalError = e.error ?? { message: e.message }; this.evalBusy = false; },
    });
  }
}
