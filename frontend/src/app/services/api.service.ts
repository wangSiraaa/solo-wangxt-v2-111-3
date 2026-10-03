import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, Subject } from 'rxjs';
import {
  BlendRequest, BlendResponse, ConstraintSpec, Material, RunDetail, RunSummary,
  SpecDiff, SpecRevision, Targets,
} from '../models/models';

/** 跨标签页导航：从历史批次跳回规范修订版。 */
@Injectable({ providedIn: 'root' })
export class NavService {
  tab$ = new Subject<'materials' | 'specs' | 'blend' | 'history'>();
  openRevision$ = new Subject<{ spec_id: number; revision_no: number }>();

  openSpecRevision(specId: number, revisionNo: number): void {
    this.openRevision$.next({ spec_id: specId, revision_no: revisionNo });
    this.tab$.next('specs');
  }
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private base = '/api';

  constructor(private http: HttpClient) {}

  materials(activeOnly = false): Observable<Material[]> {
    return this.http.get<Material[]>(`${this.base}/materials`, {
      params: activeOnly ? { active_only: true } : {},
    });
  }

  blend(req: BlendRequest): Observable<BlendResponse> {
    return this.http.post<BlendResponse>(`${this.base}/blend`, req);
  }

  evaluate(picks: { material_id: number; assay_version_id?: number | null }[],
           shares: number[], scenarioName: string): Observable<any> {
    return this.http.post(`${this.base}/evaluate`, {
      scenario_name: scenarioName, picks, shares_pct_dry: shares,
    });
  }

  runs(): Observable<RunSummary[]> {
    return this.http.get<RunSummary[]>(`${this.base}/runs`);
  }

  run(id: number): Observable<RunDetail> {
    return this.http.get<RunDetail>(`${this.base}/runs/${id}`);
  }

  // ---------- 版本化约束规范 ----------

  specs(): Observable<ConstraintSpec[]> {
    return this.http.get<ConstraintSpec[]>(`${this.base}/specs`);
  }

  createSpec(body: {
    code: string; name: string; targets: Targets;
    hazard_limits_pct: Record<string, number>; note?: string | null;
  }): Observable<ConstraintSpec> {
    return this.http.post<ConstraintSpec>(`${this.base}/specs`, body);
  }

  updateDraft(specId: number, lockVersion: number, body: {
    targets: Targets; hazard_limits_pct: Record<string, number>;
    name?: string | null; note?: string | null;
  }): Observable<ConstraintSpec> {
    return this.http.put<ConstraintSpec>(`${this.base}/specs/${specId}/draft`, {
      lock_version: lockVersion, ...body,
    });
  }

  publishSpec(specId: number, lockVersion: number, note?: string | null)
    : Observable<SpecRevision> {
    return this.http.post<SpecRevision>(`${this.base}/specs/${specId}/publish`, {
      lock_version: lockVersion, note: note ?? null,
    });
  }

  copySpec(specId: number, lockVersion: number): Observable<ConstraintSpec> {
    return this.http.post<ConstraintSpec>(`${this.base}/specs/${specId}/copy`, {
      lock_version: lockVersion,
    });
  }

  retireSpec(specId: number, lockVersion: number): Observable<ConstraintSpec> {
    return this.http.post<ConstraintSpec>(`${this.base}/specs/${specId}/retire`, {
      lock_version: lockVersion,
    });
  }

  specDiff(specId: number, fromNo: number, toNo: number): Observable<SpecDiff> {
    return this.http.get<SpecDiff>(`${this.base}/specs/${specId}/diff`, {
      params: { from_no: fromNo, to_no: toNo },
    });
  }

  revisionRuns(specId: number, revisionNo: number): Observable<{
    spec_id: number; revision_no: number;
    revision: SpecRevision; runs: RunSummary[];
  }> {
    return this.http.get<any>(
      `${this.base}/specs/${specId}/revisions/${revisionNo}/runs`);
  }
}
