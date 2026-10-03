import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import {
  BlendRequest, BlendResponse, Material, RunDetail, RunSummary,
  SpecFamily, SpecRevision, SpecSnapshot, SpecDiff,
} from '../models/models';

export interface RunRow {
  id: number; run_code: string; scenario_name: string; status: string;
  created_at: string; spec_code?: string | null; spec_revision_no?: string | null;
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
  specs(status?: 'active' | 'deprecated'): Observable<SpecFamily[]> {
    return this.http.get<SpecFamily[]>(`${this.base}/specs`,
      status ? { params: { status } } : {});
  }

  createSpec(body: { spec_code: string; name: string; note?: string;
                     snapshot: SpecSnapshot }): Observable<SpecFamily> {
    return this.http.post<SpecFamily>(`${this.base}/specs`, body);
  }

  deprecateSpec(familyId: number, lockVersion: number): Observable<SpecFamily> {
    return this.http.post<SpecFamily>(`${this.base}/specs/${familyId}/deprecate`,
      { lock_version: lockVersion });
  }

  updateDraft(revisionId: number, body: { snapshot: SpecSnapshot;
              lock_version: number | null; change_note?: string }):
      Observable<SpecRevision> {
    return this.http.patch<SpecRevision>(
      `${this.base}/spec-revisions/${revisionId}`, body);
  }

  deleteDraft(revisionId: number, lockVersion: number): Observable<void> {
    return this.http.delete<void>(`${this.base}/spec-revisions/${revisionId}`,
      { params: { lock_version: lockVersion } });
  }

  publishRevision(revisionId: number,
                  body: { lock_version: number | null;
                          family_lock_version: number | null }):
      Observable<SpecRevision> {
    return this.http.post<SpecRevision>(
      `${this.base}/spec-revisions/${revisionId}/publish`, body);
  }

  copyRevision(revisionId: number, familyLockVersion: number | null,
               changeNote?: string): Observable<SpecRevision> {
    return this.http.post<SpecRevision>(
      `${this.base}/spec-revisions/${revisionId}/copy`,
      { family_lock_version: familyLockVersion, change_note: changeNote });
  }

  revisionRuns(revisionId: number): Observable<RunRow[]> {
    return this.http.get<RunRow[]>(
      `${this.base}/spec-revisions/${revisionId}/runs`);
  }

  revisionDiff(fromId: number, toId: number): Observable<SpecDiff> {
    return this.http.get<SpecDiff>(
      `${this.base}/spec-revisions/${fromId}/diff/${toId}`);
  }
}
