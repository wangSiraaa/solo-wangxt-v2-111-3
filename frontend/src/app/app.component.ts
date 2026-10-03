import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MaterialsComponent } from './components/materials.component';
import { BlendComponent } from './components/blend.component';
import { SpecsComponent } from './components/specs.component';
import { HistoryComponent } from './components/history.component';
import { TabKey, TabService } from './services/tab.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, MaterialsComponent, BlendComponent, SpecsComponent, HistoryComponent],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
})
export class AppComponent {
  tab: TabKey = 'blend';

  constructor(tabs: TabService) {
    tabs.tab.subscribe(t => { this.tab = t; });
  }
}
