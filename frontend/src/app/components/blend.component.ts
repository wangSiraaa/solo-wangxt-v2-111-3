import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  ApiService,
} from '../services/api.service';
import { TabService } from '../services/tab.service';
import {
  BlendResponse, Material, Solution, SpecFamily, SpecRevision, Targets,
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
  modes: string[];
  cheap_id?: number;
  batch?: number;
  demo?: boolean;
  // 仅在“临时参数”模式下填入窗口/限值；规范模式仍以冻结快照为准
  targets?: Targets;
  hazards?: Record<string, number>;
}

@Component({
  selector: 'app-blend',
  standalone: true,
  imports: [CommonModule, FormsModule, SolutionCardComponent],
  templateUrl: './blend.component.html',
})
export class BlendComponent implements OnInit {
  materials: Material[] = [];
  cand: Record<number, CandRow> = {};

  // ---- 版本化约束规范 ----
  specs: SpecFamily[] = [];
  useSpec = true;
  selectedRevisionId: number | null = null;
  activeSpecRevision: SpecRevision | null = null;
  batch = 1000;
  modes = { min_cost: true, max_cheap: true, balanced: true };
  cheapId: number | null = 4;
  scenario = '含水率差异 + 多方案对比（虚构边界）';
  loading = false;
  result: BlendResponse | null = null;
  apiError: any = null;

  // 未关联规范的“临时参数”模式（保留旧能力，明确标注不可复现）
  targets: Targets = { SM: { min: 2.4, max: 2.8 }, IM: { min: 1.4, max: 1.8 }, KH: { min: 0.88, max: 0.94 } };
  hazardCl = 0.05;
  hazardAlkali = 1.5;

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
      ids: [1, 2, 3, 4, 5],
      modes: ['min_cost', 'max_cheap', 'balanced'], cheap_id: 4,
    },
    {
      key: 'cheap', label: '廉价原料致指标超限',
      desc: '仅石灰石+高碱页岩且无铁质校正：廉价料拉低成本但 IM/KH 超限，展示失败与冲突项。',
      ids: [1, 3],
      modes: ['min_cost'],
    },
    {
      key: 'alkali', label: '有害组分（碱当量）上限冲突',
      desc: '碱当量收紧到 0.40%（干基，临时参数），廉价页岩/粉煤灰无法同时满足，诊断指明突破量。',
      ids: [1, 2, 3, 4, 5],
      targets: { SM: { min: 2.4, max: 2.8 }, IM: { min: 1.4, max: 1.8 }, KH: { min: 0.88, max: 0.94 } },
      hazards: { Cl: 0.05, alkali_eq: 0.4 },
      modes: ['min_cost'],
    },
    {
      key: 'avail', label: '大批量可用量约束',
      desc: '5000 t 干生料超过石灰石 3000 t 湿基可用量，KH 与可用量同时冲突。',
      ids: [1, 2, 3, 4, 5],
      modes: ['min_cost'], batch: 5000,
    },
  ];

  constructor(private api: ApiService, private tabs: TabService) {}

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
    this.loadSpecs();
    // 从规范页“去试算引用”或历史页外跳入时，预选指定的已发布修订
    this.tabs.focusRevision.subscribe(rid => {
      if (rid != null) {
        this.selectedRevisionId = rid;
        this.useSpec = true;
        this.syncActiveRevision();
        this.tabs.clearFocus();
      }
    });
  }

  loadSpecs(): void {
    this.api.specs().subscribe(fs => {
      this.specs = fs;
      if (this.selectedRevisionId == null) {
        const firstActive = fs.find(f => f.status === 'active' && f.current_revision_id);
        this.selectedRevisionId = firstActive?.current_revision_id ?? null;
      }
      this.syncActiveRevision();
    });
  }

  /** 供模板列出的“可引用修订”：仅生效规范族的当前有效(published)修订 */
  usableRevisions(): { family: SpecFamily; rev: SpecRevision }[] {
    const out: { family: SpecFamily; rev: SpecRevision }[] = [];
    for (const f of this.specs) {
      if (f.status !== 'active' || f.current_revision_id == null) continue;
      const rev = f.revisions.find(r => r.id === f.current_revision_id);
      if (rev) out.push({ family: f, rev });
    }
    return out;
  }

  syncActiveRevision(): void {
    this.activeSpecRevision =
      this.usableRevisions().find(x => x.rev.id === this.selectedRevisionId)?.rev ?? null;
    if (!this.activeSpecRevision && this.useSpec) {
      this.useSpec = false;
    }
  }

  onSpecChange(): void {
    this.syncActiveRevision();
    this.useSpec = this.activeSpecRevision != null;
  }

  effectiveTargets(): Targets {
    return this.activeSpecRevision ? this.activeSpecRevision.spec_snapshot.targets : this.targets;
  }

  effectiveHazards(): Record<string, number> {
    return this.activeSpecRevision
      ? this.activeSpecRevision.spec_snapshot.hazard_limits_pct
      : { Cl: this.hazardCl, alkali_eq: this.hazardAlkali };
  }

  mat(id: number): Material | undefined { return this.materials.find(m => m.id === id); }

  chosenVersions(id: number) { return this.mat(id)?.assay_versions ?? []; }

  applyPreset(p: Preset): void {
    this.scenario = p.label;
    for (const m of this.materials) {
      this.cand[m.id].selected = p.ids.includes(m.id);
    }
    if (p.targets) this.targets = JSON.parse(JSON.stringify(p.targets));
    if (p.hazards) {
      this.hazardCl = p.hazards['Cl'] ?? this.hazardCl;
      this.hazardAlkali = p.hazards['alkali_eq'] ?? this.hazardAlkali;
    }
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
    if (this.useSpec && !this.activeSpecRevision) {
      this.apiError = { message: '请选择一个已发布的有效规范修订，或改用临时参数（不入库为可复现规范）。' };
      return;
    }
    this.loading = true;
    this.result = null;
    const body: any = {
      scenario_name: this.scenario,
      batch_t_dry: this.batch,
      candidates: cands.map(c => ({
        material_id: c.material_id,
        assay_version_id: c.assay_version_id,
      })),
      modes: this.selectedModes(),
      cheap_material_id: this.modes.max_cheap ? this.cheapId : null,
      save: true,
    };
    if (this.useSpec && this.activeSpecRevision) {
      // 只传规范修订 id；窗口/有害限值以服务端冻结快照为准
      body.spec_revision_id = this.activeSpecRevision.id;
    } else {
      body.targets = this.targets;
      body.hazard_limits_pct = { Cl: this.hazardCl, alkali_eq: this.hazardAlkali };
    }
    this.api.blend(body).subscribe({
      next: r => { this.result = r; this.loading = false; },
      error: e => {
        this.apiError = e.error ?? { message: '请求失败：' + e.message };
        this.loading = false;
        // 规范可能刚被停用/取代：刷新规范列表供用户改选
        if (e.error?.error_code?.startsWith('SPEC_')) this.loadSpecs();
      },
    });
  }

  goSpecs(): void { this.tabs.go('specs'); }

  readonly Object = Object;

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
