import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService, NavService } from '../services/api.service';
import { RunDetail, RunSummary } from '../models/models';

@Component({
  selector: 'app-history',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './history.component.html',
})
export class HistoryComponent implements OnInit {
  runs: RunSummary[] = [];
  detail: RunDetail | null = null;
  loading = false;

  constructor(private api: ApiService, private nav: NavService) {}

  ngOnInit(): void { this.reload(); }

  reload(): void {
    this.api.runs().subscribe(rs => { this.runs = rs; });
  }

  open(id: number): void {
    this.loading = true;
    this.api.run(id).subscribe(d => { this.detail = d; this.loading = false; });
  }

  /** 从历史批次跳回当时冻结的规范修订版（规范标签页打开并展开影响的批次）。 */
  backToSpec(): void {
    const snap = this.detail?.spec_snapshot;
    if (snap) this.nav.openSpecRevision(snap.spec_id, snap.revision_no);
  }

  hazardEntries(h: Record<string, number> | null | undefined): [string, number][] {
    return h ? Object.entries(h) : [];
  }

  ind(s: any, key: string): string {
    return s.payload?.indicators?.[key] != null
      ? Number(s.payload.indicators[key]).toFixed(3) : '—';
  }
}
