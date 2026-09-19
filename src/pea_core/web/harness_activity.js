/* One shared, accessible disclosure for all first-party PEA conversations. */
window.PeaHarnessActivity = (() => {
  function render(host, activity) {
    if (!host || !activity || activity.schema !== 'opc.pea_harness_activity.v1') return;
    host.replaceChildren();
    const details = document.createElement('details');
    details.className = 'harness-activity';
    details.dataset.state = activity.state || 'completed';
    const heading = document.createElement('summary');
    const dot = document.createElement('span'); dot.className = 'ha-indicator';
    const title = document.createElement('span'); title.className = 'ha-title'; title.textContent = activity.title || '处理过程';
    const summary = document.createElement('span'); summary.className = 'ha-summary'; summary.textContent = activity.summary || '';
    heading.append(dot, title, summary);
    const list = document.createElement('ol');
    (Array.isArray(activity.events) ? activity.events : []).forEach(event => {
      const item = document.createElement('li');
      item.dataset.state = event.state || 'completed';
      item.textContent = event.label || '';
      list.appendChild(item);
    });
    details.append(heading, list);
    host.appendChild(details);
  }
  function waiting(row) {
    const content = row && row.querySelector('.typing');
    if (!content) return;
    content.className = 'harness-working';
    content.replaceChildren();
    const dot = document.createElement('span'); dot.className = 'ha-indicator';
    const label = document.createElement('span'); label.textContent = '正在处理本轮请求…';
    content.append(dot, label);
  }
  return {render, waiting};
})();
