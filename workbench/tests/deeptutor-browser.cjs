/* Browser smoke against browser_fixture.py, using only temporary/mock data. */
const path = require('node:path');
const fs = require('node:fs');
const root = path.resolve(__dirname, '../..');
const {chromium, expect} = require(path.join(root, 'projects/project-03-deeptutor/web/node_modules/@playwright/test'));
const state = process.argv[2];
if (!state || !path.resolve(state).startsWith('/tmp/')) throw Error('Pass the /tmp fixture state directory');

(async()=>{
  const browser=await chromium.launch({headless:true, ...(process.env.VERIFICATION_BROWSER_EXECUTABLE_PATH?{executablePath:process.env.VERIFICATION_BROWSER_EXECUTABLE_PATH}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  const page=await context.newPage();
  const failures=[];
  page.on('pageerror',error=>failures.push(error.message));
  try {
    if(process.argv.includes('--service-stopped')) {
      await page.goto('http://127.0.0.1:8089/workbench/discussion?topic=browser-alpha');
      await expect(page.getByText('DeepTutor尚未连接。请启动03服务，再点击重新连接。')).toBeVisible({timeout:20000});
      await expect(page.getByRole('button',{name:'重新连接',exact:true})).toBeEnabled();
      // Display/keyboard check only, using mocked unauthenticated status;
      // this does not claim a real-account login was performed.
      await context.route('**/api/auth/status*', route=>route.fulfill({json:{enabled:true,authenticated:false}}));
      await context.route('**/api/auth/is_first_user*', route=>route.fulfill({json:{is_first_user:false}}));
      await page.evaluate(()=>{const iframe=document.createElement('iframe');iframe.title='03登录页验证';iframe.src='http://127.0.0.1:3799/login?next=%2Fworkbench-connect';iframe.style.cssText='width:100%;height:650px';document.querySelector('.discussion-page').append(iframe)});
      const login=page.frameLocator('iframe[title="03登录页验证"]');
      await expect(login.getByLabel(/Password|密码/)).toBeVisible({timeout:60000});
      await login.getByLabel(/Password|密码/).fill('offline-fixture-value');
      await page.screenshot({path:path.join(state,'service-stopped-and-login.png'),fullPage:true});
      console.log('PASS: actual stopped-backend feedback and retry; iframe native login page display/keyboard (auth status mocked, no real login submitted).');
      return;
    }
    await page.goto('http://127.0.0.1:8089/workbench/materials?topic=browser-alpha');
    if(fs.existsSync(path.join(state,'actual-context.txt'))) fs.unlinkSync(path.join(state,'actual-context.txt'));
    await page.getByRole('checkbox',{name:'选择文件夹 资料',exact:true}).check();
    await expect(page.getByRole('button',{name:'去聊天 · 1 个文件'})).toBeEnabled();
    // A user edit after selecting/handoff must be read on the next actual turn.
    fs.writeFileSync(path.join(state,'shared/topics/browser-alpha/materials/m001/正文.md'),'BROWSER_EDITED_CURRENT');
    await page.getByRole('button',{name:'去聊天 · 1 个文件'}).click();
    const iframe=page.frameLocator('iframe[title="DeepTutor研究讨论"]');
    await expect(iframe.getByRole('button',{name:/共享文件 · 正文.md/})).toBeVisible({timeout:180000});
    const collapse = iframe.getByRole('button',{name:/Collapse sidebar|收起侧栏/});
    if(await collapse.count()) {
      await collapse.click();
      await iframe.getByRole('button',{name:/Expand sidebar|展开侧栏/}).click();
    }
    const bounds=await page.locator('iframe').boundingBox();
    if(bounds.width<900 || bounds.height<600) throw Error('Embedded viewport is too narrow/short: '+JSON.stringify(bounds));
    await expect(iframe.getByRole('button',{name:/共享文件 · 正文.md/})).toHaveAttribute('title', /materials\/m001\/正文.md/);
    if(fs.existsSync(path.join(state,'actual-context.txt'))) throw Error('Preparing chat executed a turn');
    if(fs.existsSync(path.join(state,'unexpected-model-calls.txt'))) throw Error('Opening chat called a model');
    const input=iframe.locator('textarea').first();
    await input.fill('请阅读所选正文并保存一份研究笔记。');
    await input.press('Enter');
    await expect(iframe.getByText('离线模拟流式回答：已收到当前选定资料。',{exact:false}).first()).toBeVisible({timeout:30000});
    await expect(iframe.getByRole('button').filter({hasText:'生成笔记'}).first()).toBeVisible({timeout:30000});
    const actual=fs.readFileSync(path.join(state,'actual-context.txt'),'utf8');
    if(!actual.includes('BROWSER_EDITED_CURRENT')||actual.includes('OTHER_TOPIC_DO_NOT_READ')) throw Error('Actual turn context is wrong');
    await iframe.getByRole('button').filter({hasText:'生成笔记'}).first().click();
    await expect(iframe.getByRole('button',{name:'归档',exact:true})).toBeVisible();
    const download=iframe.getByRole('link',{name:/Download|下载/}).first();
    await expect(download).toBeVisible();
    const fileResponse=await context.request.get(await download.getAttribute('href').then(h=>new URL(h,'http://127.0.0.1:3799').href));
    if(!fileResponse.ok()||!(await fileResponse.text()).includes('BROWSER_GENERATED_BODY')) throw Error('Generated download failed');
    await iframe.getByRole('button',{name:'归档',exact:true}).click();
    await expect(iframe.getByRole('button',{name:'确认归档',exact:true})).toBeInViewport();
    await iframe.getByLabel('标题',{exact:true}).fill('浏览器归档笔记');
    await iframe.getByRole('button',{name:'确认归档',exact:true}).click();
    await expect(iframe.getByText('归档成功',{exact:true})).toBeVisible();
    await page.screenshot({path:path.join(state,'embedded-archive.png'),fullPage:true});
    const url=page.url();
    await page.reload();
    await expect(page.frameLocator('iframe').getByRole('button',{name:/共享文件 · 正文.md/})).toBeVisible({timeout:90000});
    if(page.url()!==url) throw Error('Deep-link changed on refresh');
    await page.getByRole('button',{name:'想法生成',exact:true}).click();
    await page.getByRole('button',{name:'已有成果',exact:true}).click();
    await expect(page.getByText('原始生成文件',{exact:true})).toBeVisible();
    await expect(page.getByText('研究笔记',{exact:true})).toBeVisible();
    await page.getByRole('button',{name:'当前课题',exact:true}).click();
    await page.getByRole('combobox',{name:'搜索课题'}).fill('browser-beta');
    await page.getByRole('listbox',{name:'课题列表'}).getByRole('option').click();
    await expect(page.getByRole('button',{name:'去聊天 · 0 个文件'})).toBeVisible();
    await page.getByRole('button',{name:'去聊天 · 0 个文件'}).click();
    await expect(page.frameLocator('iframe').getByText('浏览器乙课题',{exact:true}).first()).toBeVisible({timeout:90000});
    await expect(page.frameLocator('iframe').getByRole('button',{name:/共享文件 · /})).toHaveCount(0);
    await page.getByRole('button',{name:'想法生成',exact:true}).click();
    await page.getByRole('checkbox',{name:'选择文件夹 资料',exact:true}).check();
    await expect(page.getByRole('button',{name:'去聊天 · 1 个文件'})).toBeVisible();
    await page.getByRole('button',{name:'去聊天 · 1 个文件'}).click();
    await expect(page.frameLocator('iframe').getByRole('button',{name:/共享文件 · 正文.md/})).toBeVisible({timeout:60000});
    // A fresh standalone window opens this exact prepared session as well.
    const externalUrl=await page.getByRole('link',{name:'独立窗口打开DeepTutor'}).getAttribute('href');
    const standalone=await context.newPage();
    await standalone.goto(externalUrl);
    await expect(standalone.getByRole('button',{name:/共享文件 · 正文.md/})).toBeVisible({timeout:90000});
    await standalone.close();
    // The current archive Markdown (not its original generated copy) also
    // reaches both 04 preparation flows after a user's local edit.
    const notes=path.join(state,'shared/topics/browser-alpha/notes');
    const folders=fs.readdirSync(notes).map(name=>path.join(notes,name)).filter(p=>fs.statSync(p).isDirectory()).sort((a,b)=>fs.statSync(b).mtimeMs-fs.statSync(a).mtimeMs);
    const archived=path.join(folders[0],'归档.md');
    fs.writeFileSync(archived,'# Manual revision\nBROWSER_MANUAL_REVISION');
    const relative=path.relative(path.join(state,'shared/topics/browser-alpha'),archived);
    for(const kind of ['analysis','ideas']) {
      const response=await context.request.post('http://127.0.0.1:8089/api/workbench/topics/browser-alpha/prepare',{data:{kind,paths:[relative],options:{custom_themes:['BROWSER_MANUAL']}}});
      const prepared=await response.json();
      if(!response.ok()||!prepared.documents[0].text.includes('BROWSER_MANUAL_REVISION')) throw Error('04 did not read current archived Markdown: '+JSON.stringify(prepared));
    }
    if(fs.existsSync(path.join(state,'unexpected-model-calls.txt'))) throw Error('Archive or navigation called a model');
    if(failures.length) throw Error('Browser errors: '+failures.join('\n'));
    console.log('PASS: shared selection, iframe dimensions, native chat/WebSocket stream, current content, generated preview/download/archive, refresh/deep-link, outputs tree and topic isolation. Real model calls: 0.');
  } catch(error) {
    await page.screenshot({path:path.join(state,'failure.png'),fullPage:true});
    console.error((await page.locator('body').innerText()).slice(-1800));
    for(const frame of page.frames().slice(1)) console.error('IFRAME:',(await frame.locator('body').innerText().catch(()=>'' )).slice(-2200));
    throw error;
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
