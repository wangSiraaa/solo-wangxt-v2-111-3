import { Component, OnDestroy, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Subscription } from 'rxjs';
import { MaterialsComponent } from './components/materials.component';
import { BlendComponent } from './components/blend.component';
import { HistoryComponent } from './components/history.component';
import { SpecsComponent } from './components/specs.component';
import { NavService } from './services/api.service';

type Tab = 'materials' | 'specs' | 'blend' | 'history';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, MaterialsComponent, SpecsComponent, BlendComponent,
    HistoryComponent],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
})
export class AppComponent implements OnInit, OnDestroy {
  tab: Tab = 'blend';
  private sub = new Subscription();

  constructor(private nav: NavService) {}

  ngOnInit(): void {
    this.sub.add(this.nav.tab$.subscribe(t => { this.tab = t; }));
  }

  ngOnDestroy(): void { this.sub.unsubscribe(); }
}
