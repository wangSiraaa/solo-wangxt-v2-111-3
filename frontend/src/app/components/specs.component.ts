import { Component, OnDestroy, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { ApiService, NavService } from '../services/api.service';
import {
  ConstraintSpec, RunSummary, SpecDiff, Targets,
} from '../models/models';

interface HazardRow { key: string; limit: number; }

const EMPTY_T: Targets = {
  SM: { min: 2.4, max: 2.8 }, IM: { min: 1.4, max: 1.8 }, KH: { min: 0.88, max: 0.94 },
};

@Component({
  selector: 'app-specs',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './specs.component.html',
})
export class SpecsComponent implements OnInit, OnDestroy {
  specs: ConstraintSpec[] = [];
  sel: ConstraintSpec | null = null;
  error: any = null;
  info = '';
  busy = false;
  readonly inds = ['SM', 'IM', 'KH'] as const;

  // 草稿编辑区
  draftT: Targets = JSON.parse(JSON.stringify(EMPTY_T));
  hazardRows: HazardRow[] = [];

  // 新建规范
  newCode = '';
  newName = '';
  showCreate = false;

  // 修订版比较
  cmpFrom: number | null = null;
  cmpTo: number | null = null;
  diff: SpecDiff | null = null;

  // 某修订版影响的批次
  impactedRev: number | null = null;
  impactedRuns: RunSummary[] = [];

  private sub = new Subscription();

  constructor(private api: ApiService, private nav: NavService) {}

  ngOnInit(): void {
    this.reload();
    this.sub.add(this.nav.openRevision$.subscribe(({ spec_id, revision_no }) => {
      this.reload(() => {
        const s = this.specs.find(x => x.id === spec_id);
        if (s) {
          this.select(s);
          this.showImpacted(revision_no);
        }
      });
    }));
  }

  ngOnDestroy(): void { this.sub.unsubscribe(); }

  reload(then?: () => void): void {
    this.api.specs().subscribe(ss => {
      this.specs = ss;
      if (this.sel) {
        this.sel = ss.find(x => x.id === this.sel!.id) ?? null;
        if (this.sel && this.sel.status === 'draft') this.loadDraft(this.sel);
      }
      then?.();
    });
  }

  select(s: ConstraintSpec): void {
    this.sel = s;
    this.error = null; this.info = '';
    this.diff = null; this.impactedRev = null;
    const nos = s.revisions.map(r => r.revision_no);
    this.cmpFrom = nos.length >= 2 ? nos[nos.length - 2] : (nos[0] ?? null);
    this.cmpTo = nos.length ? nos[nos.length - 1] : null;
    if (s.status === 'draft') this.loadDraft(s);
  }

  loadDraft(s: ConstraintSpec): void {
    this.draftT = s.draft_targets
      ? JSON.parse(JSON.stringify(s.draft_targets))
      : JSON.parse(JSON.stringify(EMPTY_T));
    this.hazardRows = Object.entries(s.draft_hazard_limits_pct ?? {})
      .map(([key, limit]) => ({ key, limit }));
  }

  // ---------- 新建 / 草稿编辑 ----------

  createSpec(): void {
    this.error = null;
    if (!this.newCode.trim() || !this.newName.trim()) {
      this.error = { message: '请填写规范编号与名称。' }; return;
    }
    this.busy = true;
    this.api.createSpec({
      code: this.newCode.trim(), name: this.newName.trim(),
      targets: JSON.parse(JSON.stringify(EMPTY_T)),
      hazard_limits_pct: { Cl: 0.03, alkali_eq: 0.6 },
    }).subscribe({
      next: s => {
        this.busy = false; this.showCreate = false;
        this.newCode = ''; this.newName = '';
        this.reload(() => this.select(s));
        this.info = `已创建草稿 ${s.code}，请编辑参数后发布。`;
      },
      error: e => { this.error = e.error ?? { message: e.message }; this.busy = false; },
    });
  }

  addHazardRow(): void { this.hazardRows.push({ key: '', limit: 0 }); }
  removeHazardRow(i: number): void { this.hazardRows.splice(i, 1); }

  private hazardDict(): Record<string, number> {
    const d: Record<string, number> = {};
    for (const r of this.hazardRows) {
      if (r.key.trim()) d[r.key.trim()] = Number(r.limit);
    }
    return d;
  }

  saveDraft(): void {
    if (!this.sel) return;
    this.error = null; this.busy = true;
    this.api.updateDraft(this.sel.id, this.sel.lock_version, {
      targets: this.draftT, hazard_limits_pct: this.hazardDict(),
    }).subscribe({
      next: s => {
        this.busy = false;
        this.info = `草稿已保存（lock_version=${s.lock_version}）。`;
        this.reload(() => { if (this.sel) this.select(this.sel); });
      },
      error: e => this.handleErr(e),
    });
  }

  // ---------- 生命周期动作 ----------

  publish(): void {
    if (!this.sel) return;
    this.error = null; this.busy = true;
    this.api.publishSpec(this.sel.id, this.sel.lock_version).subscribe({
      next: rev => {
        this.busy = false;
        this.info = `已发布为不可变修订版 r${rev.revision_no}，试算可引用。`;
        this.reload(() => { if (this.sel) this.select(this.sel); });
      },
      error: e => this.handleErr(e),
    });
  }

  copyToDraft(): void {
    if (!this.sel) return;
    this.error = null; this.busy = true;
    this.api.copySpec(this.sel.id, this.sel.lock_version).subscribe({
      next: s => {
        this.busy = false;
        this.info = `已把最新修订版复制为新草稿（发布后生成 r${s.revisions.length + 1}）。`;
        this.reload(() => { if (this.sel) this.select(this.sel); });
      },
      error: e => this.handleErr(e),
    });
  }

  retire(): void {
    if (!this.sel) return;
    this.error = null; this.busy = true;
    this.api.retireSpec(this.sel.id, this.sel.lock_version).subscribe({
      next: () => {
        this.busy = false;
        this.info = '规范已停用：禁止新试算引用，历史批次仍可回看。';
        this.reload(() => { if (this.sel) this.select(this.sel); });
      },
      error: e => this.handleErr(e),
    });
  }

  /** 409 冲突：提示并给出“刷新后重试”入口。 */
  private handleErr(e: any): void {
    this.busy = false;
    this.error = e.error ?? { message: e.message };
    if (this.error?.error_code === 'SPEC_CONFLICT') {
      this.reload(() => { if (this.sel) this.select(this.sel); });
    }
  }

  // ---------- 修订版比较 / 影响批次 ----------

  compare(): void {
    if (!this.sel || this.cmpFrom == null || this.cmpTo == null) return;
    this.diff = null; this.error = null;
    this.api.specDiff(this.sel.id, this.cmpFrom, this.cmpTo).subscribe({
      next: d => { this.diff = d; },
      error: e => { this.error = e.error ?? { message: e.message }; },
    });
  }

  showImpacted(revNo: number): void {
    if (!this.sel) return;
    this.impactedRev = revNo;
    this.impactedRuns = [];
    this.api.revisionRuns(this.sel.id, revNo).subscribe(r => {
      this.impactedRuns = r.runs;
    });
  }

  diffCls(dir: string): string {
    return dir === '收紧' ? 'badge err'
      : dir === '放宽' ? 'badge ok' : 'badge warn';
  }

  statusLabel(s: string): string {
    return { draft: '草稿', published: '已发布', retired: '已停用' }[s] ?? s;
  }

  statusCls(s: string): string {
    return { draft: 'badge warn', published: 'badge ok', retired: 'badge err' }[s]
      ?? 'badge';
  }

  fmtTargets(t: Targets | null | undefined): string {
    if (!t) return '—';
    const iv = (x: any) => `[${x?.min ?? '—'}, ${x?.max ?? '—'}]`;
    return `SM ${iv(t.SM)} · IM ${iv(t.IM)} · KH ${iv(t.KH)}`;
  }

  fmtHazards(h: Record<string, number> | null | undefined): string {
    if (!h || !Object.keys(h).length) return '—';
    return Object.entries(h).map(([k, v]) => `${k}≤${v}%`).join('，');
  }
}
