import {field, feedback} from '../components/common/fields.js';
import {empty, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {handler} from '../hooks/action.js';
import {pageLabel} from '../router/routes.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

const KINDS = {created:'新增', corrected:'更正', unchanged:'无变化', conflict:'冲突'};
let lineCount = 0;

function outcomeTable(outcome) {
  const rows = [
    ...outcome.created.map(train_no => ({train_no, kind:'created', detail:'已新增该车次'})),
    ...outcome.corrected.map(train_no => ({train_no, kind:'corrected', detail:'已更新到离站时间'})),
    ...outcome.unchanged.map(train_no => ({train_no, kind:'unchanged', detail:'与已存记录一致'})),
    ...outcome.conflicts.map(item => ({train_no:item.train_no, kind:'conflict',
      detail:`${item.reason}${item.current_revision !== undefined ? `，当前版本 ${item.current_revision}` : ''}`})),
  ];
  return el('div',{class:'submit-result'},
    p('本次提交结果','eyeline'),
    table([
      {key:'train_no',label:'车次'},
      {label:'结果',render:row=>pill(KINDS[row.kind],row.kind)},
      {key:'detail',label:'说明'},
    ],rows,{caption:'本次提交结果',empty:'本次提交没有改动任何车次。'}));
}

function correctionForm(row, refresh, outcome) {
  const error = feedback();
  const submit = button('保存更正',{type:'submit',class:'quiet'});
  return el('form',{class:'fields inline-edit',onSubmit:handler(submit,error,async event=>{
    const [arrival, departure] = [...event.target.querySelectorAll('input')].map(input=>input.value.trim());
    if (!arrival || !departure) throw new Error('请填写到站和离站时间');
    outcome.replaceChildren();
    const result = await workspace.correctTrains({platform:row.platform,calls:[{
      train_no:row.train_no,arrival_at:arrival,departure_at:departure,revision:row.revision}]});
    outcome.append(outcomeTable(result));
    if (!result.conflicts.length) await refresh();
  })},el('div',{class:'form-grid'},
    field({key:`arrival-${row.id}`,label:'到站时间（本地时间）',type:'string',max_length:32},row.arrival_at),
    field({key:`departure-${row.id}`,label:'离站时间（本地时间）',type:'string',max_length:32},row.departure_at)),
    submit,el('small',{},`依据版本 ${row.revision}；若他人已更正该车次会提示冲突，不会覆盖。`),error);
}

function entryLine(prefix) {
  return el('div',{class:'form-grid call-line'},
    field({key:`${prefix}-train_no`,label:'车次',type:'string',max_length:64,required:false}),
    field({key:`${prefix}-arrival_at`,label:'到站时间（YYYY-MM-DD HH:MM:SS）',type:'string',max_length:32,required:false}),
    field({key:`${prefix}-departure_at`,label:'离站时间（YYYY-MM-DD HH:MM:SS）',type:'string',max_length:32,required:false}));
}

function readLine(row) {
  const [train_no, arrival_at, departure_at] = [...row.querySelectorAll('input')].map(input=>input.value.trim());
  return {train_no, arrival_at, departure_at};
}

function entryForm(platforms, selected, refresh) {
  const error = feedback();
  const outcome = el('div',{'aria-live':'polite'});
  const lines = el('div',{},entryLine(`call-${lineCount++}`));
  const add = button('再加一趟',{class:'quiet',onClick:()=>lines.append(entryLine(`call-${lineCount++}`))});
  const submit = button('导入或更正',{type:'submit',class:'primary','data-testid':'save-calls'});
  return panel('导入或更正到离站记录',
    p('按车次写入。已存在的车次须带最新版本号才会更正：并发提交互不相干的车次都会保留，同一车次时间冲突会逐条列出，不覆盖对方。','subtle'),
    el('form',{class:'fields','data-testid':'train-form',onSubmit:handler(submit,error,async event=>{
      const platform = event.target.querySelector('input[name="platform"]').value.trim();
      if (!platform) throw new Error('请填写站台名称');
      const calls = [...lines.querySelectorAll('.call-line')].map(readLine)
        .filter(call=>call.train_no || call.arrival_at || call.departure_at);
      if (!calls.length) throw new Error('请至少填写一趟车次');
      outcome.replaceChildren();
      const result = await workspace.correctTrains({platform,calls});
      outcome.append(outcomeTable(result));
      if (!result.conflicts.length) await refresh();
    })},
      field({key:'platform',label:'站台名称',type:'string',max_length:300},selected || platforms[0] || ''),
      lines, add, el('div',{class:'actions'},submit), error, outcome),
    el('p',{class:'subtle'},'导入后返回「滞后扫描」重新提交，即可按新记录分车况；已受理的运行仍按受理时的快照计算。'));
}

export async function trainsPage(route, refresh) {
  const {spec} = workspace;
  const listing = await workspace.trains();
  const selected = listing.platforms[route.id - 1] || listing.platforms[0];
  const calls = selected ? (await workspace.trains(selected)).calls : [];
  const outcome = el('div',{'aria-live':'polite'});
  const editable = session.canWrite;
  const picker = el('div',{class:'actions'},
    el('label',{for:'train-platform',class:'sr'},'选择站台'),
    el('select',{id:'train-platform',onChange:event=>{location.hash=`#trains/${event.target.value}`;}},
      listing.platforms.map((name,index)=>el('option',{value:index+1,selected:name===selected},name))));
  const latest = calls.reduce((value, call)=>call.updated_at > value ? call.updated_at : value, '');
  const source = listing.platforms.length
    ? panel('到离站记录',
        picker,
        p(`${selected} · 共 ${calls.length} 趟${latest ? `，最后一次更正 ${date(latest)}` : ''}`,'filename'),
        table([
          {key:'train_no',label:'车次'},
          {key:'arrival_at',label:'到站时间'},
          {key:'departure_at',label:'离站时间'},
          {key:'revision',label:'版本'},
          {key:'author',label:'最后更正'},
          {label:'操作',render:row=>editable
            ? el('details',{class:'inline-edit'},el('summary',{},'更正'),correctionForm(row,refresh,outcome))
            : '—'},
        ],calls,{caption:'到离站记录',empty:'该站台还没有到离站记录，可在右侧导入。',pageSize:20}),
        outcome)
    : panel(pageLabel(spec,'trains'),empty('还没有可维护的站台。请先在「声级与风机明细」导入带站台名称的批次。','#inputs','前往导入'));
  return el('div',{class:'two-col'},el('div',{class:'wide-panel'},source),
    el('aside',{class:'sidebar-panel'},
      editable ? entryForm(listing.platforms,selected,refresh)
        : panel('导入或更正到离站记录',p('只读角色可以查看到离站记录与版本。','subtle')),
      panel('判定口径',el('ul',{class:'method-list'},[
        '扫描受理时固化当时的到离站记录，已排队或失败重试沿用该快照。',
        '之后的更正立即用于新提交的扫描，不改变已受理运行的快照。',
        '结果页「本次扫描采用的到离站记录」列出该次扫描的版本依据。',
      ].map(text=>el('li',{},text)))),
      link('前往滞后扫描','#run',{class:'quiet'})));
}
