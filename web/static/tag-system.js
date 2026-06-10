// 实验标签和过滤系统

// 标签管理
const tagManager = {
  tags: new Map(), // runId -> tags[]
  favorites: new Set(), // runId set
  
  addTag(runId, tag) {
    if (!this.tags.has(runId)) {
      this.tags.set(runId, []);
    }
    const tags = this.tags.get(runId);
    if (!tags.includes(tag)) {
      tags.push(tag);
      this.save();
      return true;
    }
    return false;
  },
  
  removeTag(runId, tag) {
    if (this.tags.has(runId)) {
      const tags = this.tags.get(runId);
      const index = tags.indexOf(tag);
      if (index > -1) {
        tags.splice(index, 1);
        this.save();
        return true;
      }
    }
    return false;
  },
  
  getTags(runId) {
    return this.tags.get(runId) || [];
  },
  
  getAllTags() {
    const allTags = new Set();
    for (const tags of this.tags.values()) {
      tags.forEach(tag => allTags.add(tag));
    }
    return Array.from(allTags).sort();
  },
  
  toggleFavorite(runId) {
    if (this.favorites.has(runId)) {
      this.favorites.delete(runId);
    } else {
      this.favorites.add(runId);
    }
    this.save();
    return this.favorites.has(runId);
  },
  
  isFavorite(runId) {
    return this.favorites.has(runId);
  },
  
  save() {
    try {
      localStorage.setItem('locomo_tags', JSON.stringify({
        tags: Array.from(this.tags.entries()),
        favorites: Array.from(this.favorites)
      }));
    } catch (e) {
      console.error('Failed to save tags:', e);
    }
  },
  
  load() {
    try {
      const data = localStorage.getItem('locomo_tags');
      if (data) {
        const parsed = JSON.parse(data);
        this.tags = new Map(parsed.tags || []);
        this.favorites = new Set(parsed.favorites || []);
      }
    } catch (e) {
      console.error('Failed to load tags:', e);
    }
  }
};

// 初始化时加载
tagManager.load();

// 渲染标签输入
function renderTagInput(runId) {
  const tags = tagManager.getTags(runId);
  const allTags = tagManager.getAllTags();
  
  return `
    <div class="tag-input-container">
      <div class="tag-list">
        ${tags.map(tag => `
          <span class="tag">
            ${escapeHtml(tag)}
            <span class="tag-remove" onclick="removeTag('${runId}', '${escapeHtml(tag)}')">×</span>
          </span>
        `).join('')}
        <input 
          type="text" 
          class="tag-input" 
          placeholder="添加标签..."
          onkeydown="handleTagInput(event, '${runId}')"
          onfocus="showTagSuggestions('${runId}')"
          style="border: none; outline: none; padding: 6px; font-size: 12px; min-width: 100px;"
        />
      </div>
      <div class="tag-suggestions" id="tagSuggestions-${runId}" style="display: none;">
        ${allTags.map(tag => `
          <div class="tag-suggestion" onclick="addTagFromSuggestion('${runId}', '${escapeHtml(tag)}')">
            ${escapeHtml(tag)}
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

// 处理标签输入
function handleTagInput(event, runId) {
  if (event.key === 'Enter') {
    event.preventDefault();
    const input = event.target;
    const tag = input.value.trim();
    if (tag) {
      if (tagManager.addTag(runId, tag)) {
        input.value = '';
        refreshRunsList();
        toast(`标签 "${tag}" 已添加`);
      }
    }
  } else if (event.key === 'Escape') {
    event.target.blur();
    hideTagSuggestions(runId);
  }
}

// 显示标签建议
function showTagSuggestions(runId) {
  const suggestions = document.getElementById(`tagSuggestions-${runId}`);
  if (suggestions) {
    suggestions.style.display = 'block';
  }
}

// 隐藏标签建议
function hideTagSuggestions(runId) {
  const suggestions = document.getElementById(`tagSuggestions-${runId}`);
  if (suggestions) {
    suggestions.style.display = 'none';
  }
}

// 从建议添加标签
function addTagFromSuggestion(runId, tag) {
  if (tagManager.addTag(runId, tag)) {
    refreshRunsList();
    toast(`标签 "${tag}" 已添加`);
  }
  hideTagSuggestions(runId);
}

// 移除标签
function removeTag(runId, tag) {
  if (tagManager.removeTag(runId, tag)) {
    refreshRunsList();
    toast(`标签 "${tag}" 已移除`);
  }
}

// 切换收藏
function toggleFavorite(runId) {
  const isFavorite = tagManager.toggleFavorite(runId);
  refreshRunsList();
  toast(isFavorite ? '已添加到收藏' : '已从收藏移除');
}

// 渲染过滤栏
function renderFilterBar() {
  const container = document.getElementById('runFilterBar');
  if (!container) return;
  
  const allTags = tagManager.getAllTags();
  const currentFilters = state.runFilters || { tags: [], showFavorites: false };
  
  container.innerHTML = `
    <div class="filter-bar">
      <label>
        <input 
          type="checkbox" 
          ${currentFilters.showFavorites ? 'checked' : ''}
          onchange="toggleFavoriteFilter(this.checked)"
        />
        仅显示收藏
      </label>
      
      ${allTags.length > 0 ? `
        <div style="flex: 1; display: flex; gap: 8px; flex-wrap: wrap;">
          <span style="color: var(--muted); font-size: 13px;">标签过滤:</span>
          ${allTags.map(tag => `
            <label style="cursor: pointer;">
              <input 
                type="checkbox" 
                ${currentFilters.tags.includes(tag) ? 'checked' : ''}
                onchange="toggleTagFilter('${escapeHtml(tag)}', this.checked)"
              />
              ${escapeHtml(tag)}
            </label>
          `).join('')}
        </div>
      ` : ''}
      
      <button class="secondary small" onclick="clearFilters()">清除过滤</button>
    </div>
  `;
}

// 切换收藏过滤
function toggleFavoriteFilter(checked) {
  if (!state.runFilters) state.runFilters = { tags: [], showFavorites: false };
  state.runFilters.showFavorites = checked;
  applyFilters();
}

// 切换标签过滤
function toggleTagFilter(tag, checked) {
  if (!state.runFilters) state.runFilters = { tags: [], showFavorites: false };
  
  if (checked) {
    if (!state.runFilters.tags.includes(tag)) {
      state.runFilters.tags.push(tag);
    }
  } else {
    const index = state.runFilters.tags.indexOf(tag);
    if (index > -1) {
      state.runFilters.tags.splice(index, 1);
    }
  }
  
  applyFilters();
}

// 清除过滤
function clearFilters() {
  state.runFilters = { tags: [], showFavorites: false };
  renderFilterBar();
  applyFilters();
  toast('过滤已清除');
}

// 应用过滤
function applyFilters() {
  const filters = state.runFilters || { tags: [], showFavorites: false };
  const runs = state.runs || [];
  
  let filtered = runs;
  
  // 收藏过滤
  if (filters.showFavorites) {
    filtered = filtered.filter(run => tagManager.isFavorite(run.id));
  }
  
  // 标签过滤
  if (filters.tags.length > 0) {
    filtered = filtered.filter(run => {
      const runTags = tagManager.getTags(run.id);
      return filters.tags.some(tag => runTags.includes(tag));
    });
  }
  
  state.filteredRuns = filtered;
  renderRunsList(filtered);
  
  toast(`显示 ${filtered.length} / ${runs.length} 个实验`);
}

// 渲染 Runs 列表（增强版）
function renderRunsList(runs) {
  const container = document.getElementById('runList');
  if (!container) return;
  
  const runsToShow = runs || state.filteredRuns || state.runs || [];
  
  if (runsToShow.length === 0) {
    container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📭</div><div class="empty-state-title">暂无实验</div></div>';
    return;
  }
  
  container.innerHTML = runsToShow.map(run => {
    const isFavorite = tagManager.isFavorite(run.id);
    const tags = tagManager.getTags(run.id);
    const summary = run.summary || {};
    const accuracy = summary.accuracy || (summary.summary_json && summary.summary_json.accuracy_simple) || 0;
    
    return `
      <div class="run-item ${state.selectedRun && state.selectedRun.id === run.id ? 'selected' : ''}" data-run-id="${run.id}">
        <div class="run-header">
          <div class="run-title">
            <button class="favorite-btn ${isFavorite ? 'active' : ''}" onclick="toggleFavorite('${run.id}'); event.stopPropagation();">
              ${isFavorite ? '⭐' : '☆'}
            </button>
            <strong>${escapeHtml(run.name || run.id)}</strong>
          </div>
          <div class="run-score">${(accuracy * 100).toFixed(1)}%</div>
        </div>
        <div class="run-meta">
          <span>${escapeHtml(run.created_at || '')}</span>
          <span>${escapeHtml(run.kind || '')}</span>
        </div>
        ${tags.length > 0 ? `
          <div class="tag-list" style="margin-top: 8px;">
            ${tags.map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join('')}
          </div>
        ` : ''}
        <div style="margin-top: 12px;">
          ${renderTagInput(run.id)}
        </div>
      </div>
    `;
  }).join('');
  
  // 添加点击事件
  container.querySelectorAll('.run-item').forEach(item => {
    item.addEventListener('click', (e) => {
      if (!e.target.closest('.tag-input-container') && !e.target.closest('.favorite-btn')) {
        const runId = item.dataset.runId;
        const run = runsToShow.find(r => r.id === runId);
        if (run) {
          selectRun(run);
        }
      }
    });
  });
}

// 选择 Run
function selectRun(run) {
  state.selectedRun = run;
  renderRunsList();
  // 触发其他更新...
  toast(`已选择: ${run.name || run.id}`);
}

// 添加过滤栏到 Runs 页面
function addFilterBar() {
  const runsSection = document.getElementById('runs');
  if (!runsSection) return;
  
  const existingBar = document.getElementById('runFilterBar');
  if (existingBar) return;
  
  const filterBar = document.createElement('div');
  filterBar.id = 'runFilterBar';
  
  const runList = document.getElementById('runList');
  if (runList) {
    runList.parentNode.insertBefore(filterBar, runList);
  }
  
  renderFilterBar();
}

// 初始化标签系统
function initTagSystem() {
  addFilterBar();
  
  // 监听 runs 更新
  const originalRefreshRuns = window.refreshRuns;
  if (originalRefreshRuns) {
    window.refreshRuns = async function() {
      await originalRefreshRuns.call(this);
      renderFilterBar();
      applyFilters();
    };
  }
}

// 页面加载后初始化
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initTagSystem);
} else {
  initTagSystem();
}

window.TagSystem = {
  tagManager,
  renderTagInput,
  toggleFavorite,
  renderFilterBar,
  applyFilters,
  clearFilters
};
