import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService, RunRow } from '../services/api.service';
import { TabService } from '../services/tab.service';
import {
  SpecFamily, SpecRevision, SpecSnapshot, SpecDiff,
} from '../models/models';

const EMPTY_SNAPSHOT: SpecSnapshot = {
  targets: {
    SM: { min: 2.4, max: 2.8 },
    IM: { min: 1.4, max: 1.8 },
    KH: { min: 0.88, max: 0.94 },
  },
  hazard_limits_pct: { Cl: 0.05, alkali_eq: 1.5 },
};

@Component({
  selector: 'app-specs',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './specs.component.html',
})
export class SpecsComponent implements OnInit {
  families: SpecFamily[] = [];
  selectedFamilyId: number | null = null;
  selectedRevisionId: number | null = null;

  // 草稿编辑器
  editSnapshot: SpecSnapshot | null = null;
  editLock: number | null = null;
  editChangeNote = '';
  editBusy = false;

  // 新建规范
  creating = false;
  newCode = '';
  newName = '';
  newNote = '';
  newSnapshot: SpecSnapshot = JSON.parse(JSON.stringify(EMPTY_SNAPSHOT));

  actionError: any = null;
  actionInfo: string | null = null;
  busy = false;

  // 影响运行 / 版本差异
  affected: RunRow[] | null = null;
  compareRevisionId: number | null = null;
  diff: SpecDiff | null = null;

  constructor(private api: ApiService, private tabs: TabService) {}

  ngOnInit(): void {
    this.load();
    this.tabs.focusRevision.subscribe(rid => {
      if (rid != null) {
        // 等 families 加载后定位
        setTimeout(() => { this.focusRevision(rid); this.tabs.clearFocus(); }, 0);
      }
    });
  }

  load(preserveSelection = true): void {
    this.api.specs().subscribe(fs => {
      this.families = fs;
      if (!preserveSelection || this.selectedFamilyId == null) {
        this.selectedFamilyId = fs[0]?.id ?? null;
      }
      this.syncRevisionSelection();
    });
  }

  focusRevision(rid: number): void {
    const rev = this.revisionById(rid);
    if (rev) {
      this.selectedFamilyId = rev.family_id;
      this.selectedRevisionId = rid;
      this.syncRevisionSelection();
    }
  }

  revisionById(rid: number): SpecRevision | undefined {
    for (const f of this.families) {
      const r = f.revisions.find(x => x.id === rid);
      if (r) return r;
    }
    return undefined;
  }

  get family(): SpecFamily | undefined {
    return this.families.find(f => f.id === this.selectedFamilyId);
  }

  get revision(): SpecRevision | undefined {
    return this.family?.revisions.find(r => r.id === this.selectedRevisionId);
  }

  orderedRevisions(): SpecRevision[] {
    return [...(this.family?.revisions ?? [])].sort((a, b) => b.id - a.id);
  }

  selectFamily(f: SpecFamily): void {
    this.selectedFamilyId = f.id;
    this.selectedRevisionId =
      f.current_revision_id ?? f.draft_revision_id ?? f.revisions[0]?.id ?? null;
    this.syncRevisionSelection();
  }

  selectRevision(r: SpecRevision): void {
    this.selectedRevisionId = r.id;
    this.syncRevisionSelection();
  }

  syncRevisionSelection(): void {
    const rev = this.revision;
    this.affected = null;
    this.diff = null;
    this.actionError = null;
    this.compareRevisionId = null;
    if (rev?.status === 'draft') {
      this.editSnapshot = JSON.parse(JSON.stringify(rev.spec_snapshot));
      this.editLock = rev.lock_version;
      this.editChangeNote = rev.change_note ?? '';
    } else {
      this.editSnapshot = null;
    }
  }

  hazardKeys(snap: SpecSnapshot): string[] {
    return Object.keys(snap?.hazard_limits_pct ?? {});
  }

  /** 模板取率值区间，避免在模板中写索引签名 */
  iv(snap: SpecSnapshot, k: string): { min: number | null; max: number | null } {
    return (snap.targets as any)[k];
  }

  addHazardRow(): void {
    if (!this.editSnapshot) return;
    const used = new Set(Object.keys(this.editSnapshot.hazard_limits_pct));
    const candidates = ['Cl', 'alkali_eq', 'MgO', 'SO3', 'K2O', 'Na2O', 'R2O'];
    const next = candidates.find(k => !used.has(k));
    if (next) this.editSnapshot.hazard_limits_pct[next] = 0;
  }

  removeHazard(key: string): void {
    if (this.editSnapshot) delete this.editSnapshot.hazard_limits_pct[key];
  }

  private fail(e: any): void {
    this.actionError = e.error ?? { message: e.message };
    this.busy = false;
    this.editBusy = false;
  }

  // ---------- 新建 ----------
  toggleCreate(): void {
    this.creating = !this.creating;
    this.actionError = null;
    if (this.creating) {
      this.newCode = ''; this.newName = ''; this.newNote = '';
      this.newSnapshot = JSON.parse(JSON.stringify(EMPTY_SNAPSHOT));
    }
  }

  createFamily(): void {
    this.actionError = null;
    this.busy = true;
    this.api.createSpec({
      spec_code: this.newCode.trim(), name: this.newName.trim(),
      note: this.newNote.trim() || undefined, snapshot: this.newSnapshot,
    }).subscribe({
      next: f => {
        this.creating = false; this.busy = false;
        this.load(false);
        this.selectedFamilyId = f.id;
        this.selectedRevisionId = f.draft_revision_id;
        this.syncRevisionSelection();
        this.actionInfo = `已创建规范 ${f.spec_code} 的 R1 草稿，发布后才会生效。`;
      },
      error: this.fail.bind(this),
    });
  }

  // ---------- 草稿编辑 ----------
  saveDraft(): void {
    if (!this.revision || !this.editSnapshot) return;
    this.actionError = null; this.editBusy = true;
    this.api.updateDraft(this.revision.id, {
      snapshot: this.editSnapshot,
      lock_version: this.editLock,
      change_note: this.editChangeNote || undefined,
    }).subscribe({
      next: r => {
        this.editBusy = false;
        this.editLock = r.lock_version;
        this.actionInfo = '草稿已保存（仍可继续编辑，发布后冻结）。';
        this.load();
      },
      error: this.fail.bind(this),
    });
  }

  publish(): void {
    if (!this.revision || !this.family) return;
    this.actionError = null; this.busy = true;
    this.api.publishRevision(this.revision.id, {
      lock_version: this.revision.lock_version,
      family_lock_version: this.family.lock_version,
    }).subscribe({
      next: r => {
        this.busy = false;
        this.actionInfo = `修订 ${r.revision_no} 已发布并冻结，之后只能复制出新版本修改。`;
        this.load();
        this.selectedRevisionId = r.id;
        this.syncRevisionSelection();
      },
      error: this.fail.bind(this),
    });
  }

  deleteDraft(): void {
    if (!this.revision) return;
    if (!confirm(`确定删除草稿 ${this.revision.revision_no}？`)) return;
    this.actionError = null; this.busy = true;
    this.api.deleteDraft(this.revision.id, this.revision.lock_version).subscribe({
      next: () => {
        this.busy = false;
        this.selectedRevisionId = null;
        this.load();
        this.actionInfo = '草稿已删除。';
      },
      error: this.fail.bind(this),
    });
  }

  // ---------- 复制 ----------
  copyFrom(rev?: SpecRevision): void {
    const src = rev ?? this.revision;
    if (!src || !this.family) return;
    this.actionError = null; this.busy = true;
    const note = prompt('新版本调整说明（可留空）：',
      `基于 ${src.revision_no} 复制后调整限值`) ?? undefined;
    this.api.copyRevision(src.id, this.family.lock_version, note).subscribe({
      next: d => {
        this.busy = false;
        this.actionInfo = `已复制出新草稿 ${d.revision_no}，旧有效修订保持不变。`;
        this.load();
        this.selectedRevisionId = d.id;
        this.syncRevisionSelection();
      },
      error: this.fail.bind(this),
    });
  }

  // ---------- 停用 ----------
  deprecate(): void {
    if (!this.family) return;
    if (!confirm(`停用规范 ${this.family.spec_code}？停用后禁止新试算引用，历史仍可回看。`)) return;
    this.actionError = null; this.busy = true;
    this.api.deprecateSpec(this.family.id, this.family.lock_version).subscribe({
      next: () => {
        this.busy = false;
        this.actionInfo = '规范已停用：不能再发起引用它的新试算，历史批次仍可回看。';
        this.load();
      },
      error: this.fail.bind(this),
    });
  }

  // ---------- 影响运行 ----------
  loadAffected(): void {
    if (!this.revision) return;
    this.api.revisionRuns(this.revision.id).subscribe(rs => { this.affected = rs; });
  }

  goBlendWith(): void {
    // 试算只能引用当前有效(published)修订；查看草稿/历史修订时按钮指向族的有效修订
    this.tabs.go('blend', this.family?.current_revision_id ?? undefined);
  }

  // ---------- 差异 ----------
  showDiff(): void {
    if (!this.revision || !this.compareRevisionId) { this.diff = null; return; }
    this.api.revisionDiff(this.revision.id, this.compareRevisionId)
      .subscribe(d => { this.diff = d; });
  }

  statusLabel(s: string): string {
    return ({ draft: '草稿', published: '有效·已发布', superseded: '已被取代',
              deprecated: '已停用' } as Record<string, string>)[s] ?? s;
  }

  statusBadge(s: string): string {
    if (s === 'published') return 'badge ok';
    if (s === 'draft') return 'badge warn';
    return 'badge err';
  }

  fmtSnapshot(snap: SpecSnapshot): string {
    const h = Object.entries(snap.hazard_limits_pct)
      .map(([k, v]) => `${k}≤${v}`).join('，') || '无有害限值';
    const t = snap.targets;
    return `SM[${t.SM.min ?? '—'},${t.SM.max ?? '—'}] `
      + `IM[${t.IM.min ?? '—'},${t.IM.max ?? '—'}] `
      + `KH[${t.KH.min ?? '—'},${t.KH.max ?? '—'}]；${h}`;
  }
}
