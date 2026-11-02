const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.handlers={};}
 append(...items){this.children.push(...items);}
 addEventListener(name,fn){this.handlers[name]=fn;}
}
function fixture(source){
 const document={createElement:tag=>new Element(tag),createTextNode:text=>new Element('#text',text)};
 const context={window:{location:{origin:'https://phone.example'}},document,URL,setTimeout};
 vm.runInNewContext(fs.readFileSync(__dirname+'/web/markdown.js','utf8'),context);
 const root=new Element('div');context.window.ChatMarkdown.render(root,source);return root;
}
const walk=n=>[n,...n.children.flatMap(walk)],images=n=>walk(n).filter(x=>x.tag==='img');
const hash='a'.repeat(64),path='/api/media/review-image-a/poster?v='+hash;
test('a shared private poster renders as a responsive image and an authenticated same-origin link',()=>{
 for(const target of [path,'https://phone.example'+path]){
  const tree=fixture('**Thumbnail A**\n\n![Ocean cable]('+target+')');const [image]=images(tree);
  assert.equal(images(tree).length,1);assert.equal(image.src,path);assert.equal(image.alt,'Ocean cable');
  assert.equal(image.loading,'lazy');assert.equal(image.referrerPolicy,'no-referrer');
  assert.equal(walk(tree).find(x=>x.tag==='a').href,path);assert.equal(image.className,'chat-image');
 }
});
test('submitted Companion photos can be embedded without exposing a filesystem path',()=>{
 const path='/api/photos/'+'b'.repeat(32);assert.equal(images(fixture('![Photo]('+path+')'))[0].src,path);
});
test('untrusted images never cause remote, local, executable, traversal or arbitrary API requests',()=>{
 const targets=['https://other.example'+path,'//other.example'+path,'file:///Users/me/private.jpg',
 '/Users/me/private.jpg','data:image/svg+xml;base64,PHN2Zz4=','javascript:alert(1)',
 '/api/chats','/api/media/review-image-a/video','/api/media/review-image-a/captions',
 '/api/media/review-image-a/poster?token=secret',path+'#fragment',
 '/api/media/review-image-a/video/../poster','https://phone.example.evil'+path,
 'https://name:password@phone.example'+path,'/api/media/%72eview-image-a/poster'];
 for(const target of targets)assert.equal(images(fixture('![Private image]('+target+')')).length,0,target);
});
test('code examples stay inert and ordinary links/strong text keep their formatting',()=>{
 for(const source of ['`![Not an image]('+path+')`','```md\n![Not an image]('+path+')\n```','\\![Not an image]('+path+')'])assert.equal(images(fixture(source)).length,0);
 const tree=fixture('**A** [Watch](https://example.com/watch)');assert.equal(walk(tree).filter(x=>x.tag==='strong').length,1);assert.equal(walk(tree).find(x=>x.tag==='a').href,'https://example.com/watch');
});
