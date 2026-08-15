# Capstone Project: Class Scheduling System - Git & Setup Guide

<div style="background-color: #f8f9fa; padding: 15px; border-left: 5px solid #2196F3; border-radius: 5px; margin-bottom: 20px;">
    <p style="margin: 0; color: #333;"><strong>💡 Note:</strong> You can view this file in VS Code. The code blocks below are formatted in <code>bash</code>, so your text editor will automatically color the comments (lines starting with <code>#</code>) in green or grey.</p>
</div>

## 1. Cloning & Running the Project (For Groupmates / Contributors)

<p style="color: #4CAF50; font-style: italic; margin-bottom: 5px;"># Copy and paste mo ito sa VS Code terminal or Antigravity terminal</p>

```bash
git clone https://github.com/YANAMI03/ClassScheduling.git 
```

<p style="color: #4CAF50; font-style: italic; margin-bottom: 5px;"># Tapos pagtapos i-copy mo naman ito para pumasok sa folder</p>

```bash
cd ClassScheduling
```

### Environment Setup 🔐

<p style="color: #FF9800; font-style: italic; margin-bottom: 5px;"># May makikita ka na .env.example. Copy mo laman tas gawa ka .env file tapos paste mo dun yung code, tapos tanong mo kay Micko ano ang key.</p>
<p style="color: #FF9800; font-style: italic; margin-bottom: 5px;"># Bakit kailangan pa gawin yan? Meron na tlg .env file pero di mo pwede ma-download dahil sa security.</p>

### How to Run the App 🚀

<p style="color: #2196F3; font-style: italic; margin-bottom: 5px;"># Optional ito. Kung marami ka pa di na-download, baka magka-conflict kaya i-install mo muna requirements:</p>

```bash
pip install -r requirements.txt 
```

<p style="color: #2196F3; font-style: italic; margin-bottom: 5px;"># Kung alam mong kumpleto naman, dito ka na agad:</p>

```bash
python app.py 
```

<p style="color: #2196F3; font-style: italic; margin-bottom: 5px;"># Eto naman para mag-run gamit Docker. Make sure naka-open Docker mo:</p>

```bash
docker compose up --build 
```

---

## 2. Making Changes & Branching (Safe Zone)

<div style="background-color: #fff3e0; padding: 15px; border-left: 5px solid #ff9800; border-radius: 5px; margin-bottom: 20px;">
    <p style="margin: 0; color: #e65100; font-weight: bold;">⚠️ ETO IMPORTANTE WAG KA TAMARIN</p>
</div>

<p style="color: #E91E63; font-style: italic; margin-bottom: 5px;"># Eto naman para gumawa ng sarili mong branch pag may gagawin kang changes sa code.</p>
<p style="color: #E91E63; font-style: italic; margin-bottom: 5px;"># Ano yung branching? Para di mo masira yung main code na ginagawa natin. Parang safe zone mo yung branch mo.</p>

```bash
git checkout -b feature/new-scheduling-option 
```

<p style="color: #9C27B0; font-style: italic; margin-bottom: 5px;"># Ngayon kung tapos ka na, i-push mo na yung updated code sa GitHub:</p>

```bash
git add .
git commit -m "New scheduling option"
git push origin feature/new-scheduling-option 
```

---

## 3. Troubleshooting Errors 🛠️

### Error: "Author identity unknown"
<p style="color: #F44336; font-style: italic; margin-bottom: 5px;"># I-setup mo lang yung git config mo. Palitan mo lang ng email at name mo:</p>

```bash
git config --global user.email "you@example.com"
git config --global user.name "Your Name" 
```

### Error: "Updates were rejected..."
<p style="color: #F44336; font-style: italic; margin-bottom: 5px;"># Baka dahil gumawa ng change yung iba. I-pull mo lang yung code na ginawa ng iba para updated ang hawak mong code:</p>

```bash
git pull origin feature/new-scheduling-option 
```

---