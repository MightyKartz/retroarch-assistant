const test=require('node:test');const assert=require('node:assert');const fs=require('node:fs');const vm=require('node:vm');
function runtime(){const context={module:{exports:{}},workbenchText:zh=>zh,currentUILanguage:'zh',showModal(d){d.showModal();}};vm.createContext(context);vm.runInContext(fs.readFileSync('src/templates/game-files.js','utf8'),context);return context.module.exports;}

test('gameFilesSorted keeps folders first in both directions',()=>{
    const {gameFilesSorted}=runtime();
    const items=[{name:'b.sfc',directory:false},{name:'Zeta',directory:true},{name:'a.sfc',directory:false},{name:'Alpha',directory:true}];
    assert.deepEqual(gameFilesSorted(items,'asc').map(i=>i.name),['Alpha','Zeta','a.sfc','b.sfc']);
    assert.deepEqual(gameFilesSorted(items,'desc').map(i=>i.name),['Zeta','Alpha','b.sfc','a.sfc']);
    assert.deepEqual(gameFilesSorted(items).map(i=>i.name),['Alpha','Zeta','a.sfc','b.sfc']);
});

test('fileChangeRegistrations maps batch delete results to sync changes',()=>{
    const {fileChangeRegistrations}=runtime();
    const result={items:[
        {path:'/d/Game',folder:true,status:'deleted',deleted:['/d/Game/a.bin','/d/Game/b.bin']},
        {path:'/d/Part',folder:true,status:'partial',deleted:['/d/Part/x.bin']},
        {path:'/d/solo.sfc',folder:false,status:'deleted',deleted:['/d/solo.sfc']},
        {path:'/d/later.sfc',folder:false,status:'pending',deleted:[]},
        {path:'/d/fail.sfc',folder:false,status:'failed',deleted:[]},
    ]};
    assert.deepEqual(fileChangeRegistrations(result),[
        {action:'delete',path:'/d/Game',folder:true},
        {action:'delete',path:'/d/Part/x.bin',folder:false},
        {action:'delete',path:'/d/solo.sfc',folder:false},
    ]);
    assert.deepEqual(fileChangeRegistrations(null),[]);
});

test('gameFilesDropDirectory rejects stale, busy or vanished targets',()=>{
    const {gameFilesDropDirectory}=runtime();
    const current={path:'/A',items:[{path:'/A/sub',directory:true},{path:'/A/f.sfc',directory:false}]};
    const zone=(over={})=>({dropped:true,nav:2,context:'/A',directory:'/A',stamp:42,...over});
    assert.equal(gameFilesDropDirectory(zone(),current,2,false,42),'/A');
    assert.equal(gameFilesDropDirectory(zone({directory:'/A/sub'}),current,2,false,42),'/A/sub');
    assert.equal(gameFilesDropDirectory(null,current,2,false,42),null);
    assert.equal(gameFilesDropDirectory(zone({dropped:false}),current,2,false,42),null);
    assert.equal(gameFilesDropDirectory(zone({nav:1}),current,2,false,42),null);
    assert.equal(gameFilesDropDirectory(zone(),current,2,true,42),null);
    assert.equal(gameFilesDropDirectory(zone({context:'/B'}),current,2,false,42),null);
    assert.equal(gameFilesDropDirectory(zone({directory:''}),current,2,false,42),null);
    assert.equal(gameFilesDropDirectory(zone({directory:'/A/gone'}),current,2,false,42),null);
    assert.equal(gameFilesDropDirectory(zone({directory:'/A/f.sfc'}),current,2,false,42),null);
    assert.equal(gameFilesDropDirectory(zone(),null,2,false,42),null);
    // A late callback from an older drop never consumes a newer zone.
    assert.equal(gameFilesDropDirectory(zone(),current,2,false,7),null);
    assert.equal(gameFilesDropDirectory(zone(),current,2,false,null),null);
    assert.equal(gameFilesDropDirectory(zone({stamp:undefined}),current,2,false,undefined),null);
});
