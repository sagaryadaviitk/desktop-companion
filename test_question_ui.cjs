const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(__dirname + '/web/app.js', 'utf8');
function fixture() {
  class Node {
    constructor(tag) {
      this.tag = tag; this.handlers = {}; this.children = []; this.dataset = {}; this.value = '';
    }
    append(...children) { this.children.push(...children); }
    replaceChildren() { this.children = []; }
    addEventListener(name, fn) { this.handlers[name] = fn; }
    setAttribute() {}
    close() { this.open = false; }
    querySelector() { return this.action ||= new Node('strong'); }
  }
  const nodes = new Map();
  const el = id => { if (!nodes.has(id)) nodes.set(id, new Node(id)); return nodes.get(id); };
  const questions = ['one','two'].map(id => ({id,revision:'v1',delivery:'unanswered',options:[],title:id}));
  const context = {selected:'chat',selectedData:{questions,control:{available:true,live:{}}},
    connected:true,authVersion:1,csrf:'fake',questionSignature:'',chatRequestVersion:4,
    document:{body:{classList:{contains:()=>false}},createElement: tag=>new Node(tag)},
    questionDrafts:new Map(),questionBusy:new Set(),questionErrors:new Map(),queueSteering:new Set(),
    api:async()=>({state:'steered'}),renderApprovals(){},showCommandResult(){},el,
    refreshChat:async fresh=>assert.equal(fresh,true)};
  vm.runInNewContext(source.slice(source.indexOf('function renderQuestions(){'),source.indexOf("el('questions-banner').addEventListener")),context);
  vm.runInNewContext(source.slice(source.indexOf('function questionCount(chat)'),source.indexOf('function approvalCount(chat)')),context);
  el('questions-dialog').open=true;
  context.renderQuestions();
  return {context,el};
}
test('mobile answer retires only its card, fences an older poll and closes the last dialog',async()=>{
  const {context,el}=fixture();
  for(const remaining of [1,0]){
    const form=el('question-cards').children[0].children.find(x=>x.tag==='form');
    const input=form.children.find(x=>x.tag==='textarea');
    input.value='Okay';input.handlers.input();
    await form.handlers.submit({preventDefault(){}});
    assert.equal(context.selectedData.questions.length,remaining);
    assert.equal(el('questions-dialog').open,Boolean(remaining));
    assert.equal(el('questions-banner').hidden,!remaining);
    if(remaining)assert.equal(context.selectedData.questions[0].id,'two');
  }
  assert.equal(context.chatRequestVersion,6);
});
test('badge counts only exact unanswered IDs present in the desktop observation',()=>{
  const {context}=fixture(), chat={id:'chat',question_count:1,unanswered_question_ids:['two']};
  context.currentStatus={execution:{chat:{question_count:1,async_question_ids:['one']}}};
  assert.equal(context.questionCount(chat),0);
  context.currentStatus.execution.chat.async_question_ids.push('two');
  assert.equal(context.questionCount(chat),1);
  context.connected=false;assert.equal(context.questionCount(chat),1);
});
