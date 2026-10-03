import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

export type TabKey = 'materials' | 'specs' | 'blend' | 'history';

/** 跨页跳转：如历史批次“回到当时冻结规则” → 规范管理页并选中该修订。 */
@Injectable({ providedIn: 'root' })
export class TabService {
  private tab$ = new BehaviorSubject<TabKey>('blend');
  private focusRevision$ = new BehaviorSubject<number | null>(null);

  get tab(): BehaviorSubject<TabKey> { return this.tab$; }
  get focusRevision(): BehaviorSubject<number | null> { return this.focusRevision$; }

  go(tab: TabKey, revisionId?: number): void {
    if (revisionId != null) this.focusRevision$.next(revisionId);
    this.tab$.next(tab);
  }

  /** 目标页消费完跳转意图后清空，避免下次进入被旧值重定位。 */
  clearFocus(): void {
    if (this.focusRevision$.value != null) this.focusRevision$.next(null);
  }
}
