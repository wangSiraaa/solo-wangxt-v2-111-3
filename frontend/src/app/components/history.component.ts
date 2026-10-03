import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../services/api.service';
import { TabService } from '../services/tab.service';
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

  constructor(private api: ApiService, private tabs: TabService) {}

  ngOnInit(): void { this.reload(); }

  reload(): void {
    this.api.runs().subscribe(rs => { this.runs = rs; });
  }

  open(id: number): void {
    this.loading = true;
    this.api.run(id).subscribe(d => { this.detail = d; this.loading = false; });
  }

  /** 从历史批次回到当时冻结的规范修订（即使该修订现已被取代/停用也可只读回看）。 */
  backToFrozenRule(): void {
    if (this.detail?.spec?.revision_id) {
      this.tabs.go('specs', this.detail.spec.revision_id);
    }
  }

  ind(s: any, key: string): string {
    return s.payload?.indicators?.[key] != null
      ? Number(s.payload.indicators[key]).toFixed(3) : '—';
  }

  readonly Object = Object;
}
