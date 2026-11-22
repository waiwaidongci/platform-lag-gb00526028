import {plot} from '../components/common/chart.js';
import {feedback} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {REVIEW, STATUS} from '../constants/enums.js';
import {handler, poll} from '../hooks/action.js';
import {pageLabel} from '../router/routes.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

export async function results(route, refresh) {
  const {spec,runs,datasets} = workspace;
  const id = route.id || runs[0]?.id;
  if (!id) return panel(pageLabel(spec,'results'),empty('还没有可查看的运行结果。','#run','前往运行'));
  const job = await workspace.getRun(id);
  const source = datasets.find(item => item.id === job.dataset_id);
  const message = feedback();
  const finished = item => item.status === 'complete' || item.status === 'failed';
  if (!finished(job)) {
    const original = location.hash;
    const status = el('p',{role:'status'},'后台正在计算，完成后会自动展示。');
    poll(()=>workspace.getRun(id),finished,()=>location.hash===original)
      .then(result=>{if (result && status.isConnected && location.hash===original) refresh();
        else if(status.isConnected) status.textContent='仍在执行，请点击刷新检查。';})
      .catch(error=>{if(status.isConnected) status.textContent=error.message;});
    return panel(job.name,pill(STATUS[job.status],job.status),status,button('刷新',{class:'quiet',onClick:refresh}));
  }
  const download = button('下载结果 CSV',{class:'quiet','data-testid':'export',onClick:event=>
    handler(download,message,()=>workspace.download(`/runs/${job.id}/export`,`${spec.short_name}-${job.id}.csv`))(event)});
  const history = el('label',{class:'field run-picker',for:'run-picker'},el('span',{},'切换运行记录'),
    el('select',{id:'run-picker',onChange:event=>{location.hash=`#results/${event.target.value}`;}},
      (runs.some(item=>item.id===job.id) ? runs : [job,...runs]).map(item=>el('option',{value:item.id,selected:item.id===job.id},item.name))));
  const result = job.result;
  const analysis = result ? el('section',{class:'analysis','data-testid':'analysis-complete'},
    el('div',{class:'result-head'},p(job.name,'eyeline'),h(2,result.headline),p(result.note,'intro-copy'),
      el('div',{class:'actions'},pill(STATUS[job.status],job.status),download,message)),
    metrics(result.metrics),
    result.conditions && conditionsPanel(result.conditions),
    result.charts.map(plot),
    (result.sections || []).map(item=>panel(item.title,p(item.text))),
    result.tables.map(item=>panel(item.title,table(item.columns,item.rows,{caption:item.title}))))
    : panel(job.name,pill(STATUS[job.status],job.status),p(job.error,'feedback'),link('返回调整参数',`#run/${job.dataset_id}`,{class:'quiet'}));
  const inputs = spec.params.map(param=>el('div',{},el('dt',{},param.label),el('dd',{},value(job.params[param.key]))));
  const snapshot = job.timetable;
  const timetablePanel = result && panel('本次扫描的车次依据',
    p(snapshot
      ? `${snapshot.platform || '—'} · ${snapshot.available ? `受理时共 ${snapshot.call_count} 趟到离站记录` : '受理时该站台尚未维护到离站记录'}${snapshot.captured_at ? `，快照时间 ${date(snapshot.captured_at)}` : ''}。`
      : '该运行早于到离站快照功能，没有保存当时的车次依据。','subtle'),
    p('之后的更正只影响新提交的扫描。','subtle'),
    snapshot?.calls?.length && el('details',{},
      el('summary',{},`查看 ${snapshot.calls.length} 趟车次与版本`),
      table([
        {key:'train_no',label:'车次'}, {key:'arrival_at',label:'到站时间'},
        {key:'departure_at',label:'离站时间'}, {key:'revision',label:'版本'},
      ],snapshot.calls,{caption:'本次扫描采用的到离站记录',pageSize:16})));
  return el('div',{},el('div',{class:'two-col'},el('div',{class:'wide-panel'},analysis),
    el('aside',{class:'sidebar-panel'},panel('运行来源',history,
      source && link(source.name,`#inputs/${source.id}`),p(date(job.finished_at),'subtle'),
      el('dl',{class:'dictionary'},inputs)),
    timetablePanel,
    result && panel(spec.decision, pill(REVIEW[job.review],job.review),
      link('打开复核记录',`#review/${job.id}`,{class:'quiet'})))));
}

function conditionsTable(conditions) {
  if (!conditions.available) {
    return p('本次扫描没有可用的到离站记录，未能分车况。导入或更正后重新提交，即可分别查看两组峰值。','subtle');
  }
  const rows = [conditions.train, conditions.idle].map(group=>({
    label:`${group.label}组`,
    lag:group.peak ? `${group.peak.lag_seconds > 0 ? '+' : ''}${group.peak.lag_seconds} 秒` : '—',
    coefficient:group.peak ? group.peak.pearson_r : null,
    pairs:group.peak ? group.peak.pair_count : group.max_pairs,
    note:group.peak ? '达到配对下限，按 |r| 取候选峰' : group.reason,
  }));
  return el('div',{},
    table([
      {key:'label',label:'车况'},
      {key:'lag',label:'候选峰滞后'},
      {key:'coefficient',label:'Pearson r'},
      {key:'pairs',label:'候选峰配对'},
      {key:'note',label:'说明'},
    ],rows,{caption:'分车况候选峰'}),
    p(conditions.comparison,'subtle'),
    conditions.cross_pairs
      ? p(`跨车况配对 ${conditions.cross_pairs} 对（零滞后）单列，不计入上述两组。`,'subtle')
      : null);
}

function conditionsPanel(conditions) {
  return panel('有车与无车两组峰值', conditionsTable(conditions));
}
