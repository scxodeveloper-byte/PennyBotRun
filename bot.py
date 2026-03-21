import discord
from discord import app_commands, ui
import asyncio
import os
import json
from datetime import datetime, timezone
from dotenv import load_dotenv
import aiohttp
from typing import List, Dict, Optional
import threading
from flask import Flask

# ────────────────────────────────────────────────
#   Constants
# ────────────────────────────────────────────────
REQUESTER_ROLE_ID    = 1332006727830339594
APPROVER_ROLE_ID     = 1331831112963461120
TARGET_ROLE_1_ID     = 1332058188933103677
TARGET_ROLE_2_ID     = 935023208946606081
APPROVAL_CHANNEL_ID  = 1468197242186764381

# Role IDs for hourly management
HOURLY_CHECK_ROLE_ID = 959996960834748416

# Roles to add if they don't have them (when they have HOURLY_CHECK_ROLE_ID)
ROLES_TO_ADD = [
    1467443766423064641,
    1467443606028816502,
    1467443960996958219,
    1467452194960707697,
    1467452045132038284,
    1467450300242595840,
    1467444038645841941,
    1467444148540932179,
    1467444235853762714
]

# Roles that should remove all the above roles + HOURLY_CHECK_ROLE_ID
ROLES_THAT_REMOVE = [
    1332058188933103677,
    935023208946606081,
    1433102554840957010,
    1332058285817466971,
    1331957308703375401
]

# Special case roles that add another role
SPECIAL_ROLE_1 = 1331826865744248892
SPECIAL_ROLE_2 = 959997171648835594
SPECIAL_ROLE_TO_ADD = 1467443465041477764

# Apps Script Web App URLs
APPS_SCRIPT_WEB_APP_URL = ""
PERSONNEL_SCRIPT_URL = ""

# ────────────────────────────────────────────────
#   Flask Web Server for Keep Alive
# ────────────────────────────────────────────────
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_webserver():
    app.run(host='0.0.0.0', port=8080)

# Start web server in background
threading.Thread(target=run_webserver, daemon=True).start()
print("🌐 Web server started on port 8080")

# ────────────────────────────────────────────────
#   1. Load token securely
# ────────────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
APPS_SCRIPT_WEB_APP_URL = os.getenv("APPS_SCRIPT_WEB_APP_URL", APPS_SCRIPT_WEB_APP_URL)
PERSONNEL_SCRIPT_URL = os.getenv("PERSONNEL_SCRIPT_URL", PERSONNEL_SCRIPT_URL)

if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in .env file!")

if not APPS_SCRIPT_WEB_APP_URL or APPS_SCRIPT_WEB_APP_URL == "YOUR_WEB_APP_URL_HERE":
    raise ValueError("APPS_SCRIPT_WEB_APP_URL not configured in .env file!")

if not PERSONNEL_SCRIPT_URL or PERSONNEL_SCRIPT_URL == "":
    print("ℹ️ PERSONNEL_SCRIPT_URL not configured. Profile command will be disabled.")
else:
    print(f"✅ PERSONNEL_SCRIPT_URL configured")

# ────────────────────────────────────────────────
#   2. Apps Script API Helper Functions (Medals)
# ────────────────────────────────────────────────
async def call_apps_script(function_name: str, data: dict = None):
    """Call Apps Script web app function"""
    try:
        async with aiohttp.ClientSession() as session:
            params = {'function': function_name}
            if data:
                params.update(data)
            
            print(f"📡 Calling Apps Script: {function_name}")
            
            async with session.get(APPS_SCRIPT_WEB_APP_URL, params=params) as response:
                response_text = await response.text()
                print(f"📡 Response: {response.status}")
                
                if response.status == 200:
                    try:
                        return json.loads(response_text)
                    except json.JSONDecodeError:
                        print(f"⚠️ Failed to parse JSON")
                        return None
                else:
                    print(f"❌ Error calling {function_name}: {response.status}")
                    return None
    except Exception as e:
        print(f"💥 Exception calling Apps Script: {e}")
        return None

async def find_user_row(user_id: str) -> Optional[int]:
    """Find the row number for a user ID in column A"""
    result = await call_apps_script('findUserRow', {'userId': user_id})
    if result and result.get('success'):
        row = result.get('row')
        return row if row != -1 else None
    return None

async def add_user_to_sheet(user_id: str) -> int:
    """Add a new user to column A (empty medals)"""
    result = await call_apps_script('addUser', {'userId': user_id})
    if result and result.get('success'):
        return result.get('row')
    raise Exception("Failed to add user")

async def get_user_medals(user_id: str) -> List[str]:
    """Get all medals for a user (Y in their row)"""
    result = await call_apps_script('getUserMedals', {'userId': user_id})
    if result and result.get('success'):
        return result.get('medals', [])
    return []

async def update_medal_for_user(user_id: str, medal_name: str, has_medal: bool) -> bool:
    """Update a user's medal status (set Y or empty)"""
    result = await call_apps_script('updateMedal', {
        'userId': user_id,
        'medalName': medal_name,
        'hasMedal': 'true' if has_medal else 'false'
    })
    return bool(result and result.get('success'))

async def get_all_medal_types() -> List[str]:
    """Get all medal types from row 1"""
    result = await call_apps_script('getAllMedalTypes')
    if result and result.get('success'):
        return result.get('medals', [])
    return []

async def add_medal_type(medal_name: str) -> bool:
    """Add a new medal type to row 1"""
    result = await call_apps_script('addMedalType', {'medalName': medal_name})
    return bool(result and result.get('success'))

async def delete_medal_type(medal_name: str) -> bool:
    """Delete a medal type from row 1"""
    result = await call_apps_script('deleteMedalType', {'medalName': medal_name})
    return bool(result and result.get('success'))

async def get_medal_stats():
    """Get medal statistics"""
    result = await call_apps_script('getMedalStats')
    return result

# ────────────────────────────────────────────────
#   3. Personnel Status API Helper Functions
# ────────────────────────────────────────────────
async def call_personnel_script(function_name: str, data: dict = None):
    """Call Personnel Status Apps Script web app"""
    if not PERSONNEL_SCRIPT_URL:
        print("❌ PERSONNEL_SCRIPT_URL not configured")
        return None
        
    try:
        async with aiohttp.ClientSession() as session:
            params = {'function': function_name}
            if data:
                params.update(data)
            
            print(f"📡 Calling Personnel Script: {function_name}")
            
            async with session.get(PERSONNEL_SCRIPT_URL, params=params) as response:
                response_text = await response.text()
                print(f"📡 Personnel Script Response: {response.status}")
                
                if response.status == 200:
                    try:
                        return json.loads(response_text)
                    except json.JSONDecodeError:
                        print(f"⚠️ Failed to parse JSON from personnel script")
                        return None
                else:
                    print(f"❌ Error calling personnel script {function_name}: {response.status}")
                    return None
    except Exception as e:
        print(f"💥 Exception calling personnel script: {e}")
        return None

async def find_personnel(rp_name: str):
    """Find personnel by RP name in the personnel sheets"""
    result = await call_personnel_script('findPersonnel', {'rpName': rp_name})
    return result

# ────────────────────────────────────────────────
#   4. Bot setup
# ────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ────────────────────────────────────────────────
#   5. Hourly Role Management Task
# ────────────────────────────────────────────────
async def hourly_role_management():
    """Check every hour and manage roles based on criteria"""
    await bot.wait_until_ready()
    
    print("⏰ Waiting 2 minutes before starting hourly role management...")
    await asyncio.sleep(120)
    
    while not bot.is_closed():
        try:
            print(f"🕐 Starting hourly role check...")
            
            for guild in bot.guilds:
                try:
                    members = []
                    async for member in guild.fetch_members(limit=None):
                        members.append(member)
                    
                    print(f"👥 Checking {len(members)} members in {guild.name}")
                    
                    processed_count = 0
                    for member in members:
                        try:
                            member_role_ids = [role.id for role in member.roles]
                            has_hourly_check_role = HOURLY_CHECK_ROLE_ID in member_role_ids
                            has_remove_trigger_role = any(role_id in member_role_ids for role_id in ROLES_THAT_REMOVE)
                            
                            if has_remove_trigger_role:
                                roles_to_remove = []
                                for role_id in ROLES_TO_ADD + [HOURLY_CHECK_ROLE_ID]:
                                    if role_id in member_role_ids:
                                        role = guild.get_role(role_id)
                                        if role:
                                            roles_to_remove.append(role)
                                
                                if roles_to_remove:
                                    try:
                                        await member.remove_roles(*roles_to_remove, reason="Hourly role cleanup")
                                        print(f"  🔄 Removed roles from {member.display_name}")
                                    except Exception as e:
                                        print(f"  ❌ Error removing roles: {e}")
                                
                                processed_count += 1
                                continue
                            
                            if has_hourly_check_role:
                                roles_to_add = []
                                for role_id in ROLES_TO_ADD:
                                    if role_id not in member_role_ids:
                                        role = guild.get_role(role_id)
                                        if role:
                                            roles_to_add.append(role)
                                
                                if roles_to_add:
                                    try:
                                        await member.add_roles(*roles_to_add, reason="Hourly role assignment")
                                        print(f"  🔄 Added roles to {member.display_name}")
                                    except Exception as e:
                                        print(f"  ❌ Error adding roles: {e}")
                                
                                processed_count += 1
                            
                            has_special_role = (SPECIAL_ROLE_1 in member_role_ids) or (SPECIAL_ROLE_2 in member_role_ids)
                            special_role = guild.get_role(SPECIAL_ROLE_TO_ADD)
                            
                            if has_special_role and special_role:
                                if SPECIAL_ROLE_TO_ADD not in member_role_ids:
                                    try:
                                        await member.add_roles(special_role, reason="Hourly role assignment")
                                        print(f"  ⭐ Added special role to {member.display_name}")
                                    except Exception as e:
                                        print(f"  ❌ Error adding special role: {e}")
                                processed_count += 1
                            elif special_role and SPECIAL_ROLE_TO_ADD in member_role_ids:
                                try:
                                    await member.remove_roles(special_role, reason="Hourly role cleanup")
                                    print(f"  ⭐ Removed special role from {member.display_name}")
                                except Exception as e:
                                    print(f"  ❌ Error removing special role: {e}")
                                processed_count += 1
                                
                        except Exception as e:
                            print(f"  ⚠️ Error processing {member.display_name}: {e}")
                            continue
                    
                    print(f"✅ Completed hourly check for {guild.name}: Processed {processed_count} members")
                    
                except Exception as e:
                    print(f"⚠️ Error processing guild {guild.name}: {e}")
                    continue
            
            print(f"🕐 Hourly role check completed. Waiting 1 hour...")
            
        except Exception as e:
            print(f"💥 Critical error in hourly_role_management: {e}")
        
        await asyncio.sleep(3600)

# ────────────────────────────────────────────────
#   6. Discharge Modal
# ────────────────────────────────────────────────
class DischargeModal(ui.Modal, title="Discharge Request"):
    user_ids = ui.TextInput(
        label="User IDs (space separated)",
        style=discord.TextStyle.paragraph,
        placeholder="123456789012345678 987654321098765432 ...",
        required=True,
        max_length=1024
    )
    reason = ui.TextInput(
        label="Reason",
        style=discord.TextStyle.short,
        placeholder="Inactivity / Rule violation / etc.",
        required=True,
        max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not any(role.id == REQUESTER_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("You lack permission to request discharges.", ephemeral=True)
            return

        id_list = self.user_ids.value.split()
        if not id_list:
            await interaction.response.send_message("At least one user ID is required.", ephemeral=True)
            return

        guild = interaction.guild
        targets = []
        errors = []
        hierarchy_violations = []

        requester_role_ids = [role.id for role in interaction.user.roles]
        requester_highest_role = interaction.user.top_role

        for id_str in id_list:
            try:
                uid = int(id_str.strip())
                member = await guild.fetch_member(uid)
                
                if not interaction.user.guild_permissions.administrator:
                    target_has_higher_role = False
                    higher_roles = []
                    
                    for role in member.roles:
                        if role.id not in requester_role_ids and role > requester_highest_role:
                            target_has_higher_role = True
                            higher_roles.append(role.name)
                    
                    if target_has_higher_role:
                        hierarchy_violations.append(f"{member.mention} has higher role(s): {', '.join(higher_roles)}")
                        continue
                
                targets.append(member)
            except ValueError:
                errors.append(f"Invalid ID: {id_str}")
            except discord.NotFound:
                errors.append(f"Member not found: {id_str}")
            except Exception as e:
                errors.append(f"Error ({id_str}): {str(e)}")

        if hierarchy_violations:
            error_message = "**Cannot discharge members with higher roles:**\n"
            error_message += "\n".join(hierarchy_violations)
            
            if targets:
                error_message += "\n\n**Note:** Other valid targets were ignored due to hierarchy violations."
            
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        if errors and not targets:
            await interaction.response.send_message("No valid members found.\n" + "\n".join(errors), ephemeral=True)
            return

        if not targets:
            await interaction.response.send_message("No valid targets to discharge.", ephemeral=True)
            return

        approval_channel = guild.get_channel(APPROVAL_CHANNEL_ID)
        if not approval_channel:
            await interaction.response.send_message("Approval channel not found.", ephemeral=True)
            return

        embed = discord.Embed(
            title="USS Pennsylvania Discharge Request",
            description=(
                f"**Requested by:** {interaction.user.mention} ({interaction.user.top_role.name})\n\n"
                f"**Targets:**\n" + "\n".join(f"{m.mention} ({m.top_role.name})" for m in targets) + "\n\n"
                f"**Reason:** {self.reason.value}"
            ),
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Role Hierarchy Check", 
            value="✅ All targets have lower roles than requester", 
            inline=False
        )

        view = DischargeApprovalView(targets, self.reason.value)

        await approval_channel.send(
            content=f"<@&{APPROVER_ROLE_ID}> New discharge request requires review!",
            embed=embed,
            view=view
        )

        await interaction.response.send_message("Request submitted for review.", ephemeral=True)

# ────────────────────────────────────────────────
#   7. Discharge Approval View
# ────────────────────────────────────────────────
class DischargeApprovalView(ui.View):
    def __init__(self, targets: list[discord.Member], reason: str):
        super().__init__(timeout=None)
        self.targets = targets
        self.reason = reason
        self.new_nickname = f"Discharged for {reason}"

    @ui.button(label="Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        if not any(role.id == APPROVER_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("Only approved personnel can confirm.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        await interaction.message.edit(embed=embed, view=None)

        guild = interaction.guild
        role1 = guild.get_role(TARGET_ROLE_1_ID)
        role2 = guild.get_role(TARGET_ROLE_2_ID)

        if not role1 or not role2:
            await interaction.response.send_message("One or both target roles are missing.", ephemeral=True)
            return

        success = 0
        errors = []

        for member in self.targets:
            try:
                await member.edit(nick=self.new_nickname, reason=f"Discharge approved - {self.reason}")
                await member.edit(roles=[role1, role2], reason=f"Discharge approved - {self.reason}")
                success += 1
            except discord.Forbidden as e:
                errors.append(f"{member.mention}: Missing permissions ({str(e)})")
            except discord.HTTPException as e:
                errors.append(f"{member.mention}: API error ({str(e)})")
            except Exception as e:
                errors.append(f"{member.mention}: Unexpected error ({str(e)})")

        msg = f"**Approved** — Processed {success}/{len(self.targets)} users.\nNickname set to: `{self.new_nickname}`"
        if errors:
            msg += "\n\n**Errors:**\n" + "\n".join(errors)

        await interaction.response.send_message(msg, ephemeral=True)

    @ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: ui.Button):
        if not any(role.id == APPROVER_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("Only approved personnel can confirm.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        await interaction.message.edit(embed=embed, view=None)

        await interaction.response.send_message("Request **denied**.", ephemeral=True)

# ────────────────────────────────────────────────
#   8. Medal Award Modal
# ────────────────────────────────────────────────
class MedalAwardModal(ui.Modal, title="Medal Award Request"):
    user_ids = ui.TextInput(
        label="User IDs (space separated)",
        style=discord.TextStyle.paragraph,
        placeholder="123456789012345678 987654321098765432 ...",
        required=True,
        max_length=1024
    )
    medal_name = ui.TextInput(
        label="Medal Name",
        style=discord.TextStyle.short,
        placeholder="Purple Heart / Medal of Honor / etc.",
        required=True,
        max_length=100
    )
    reason = ui.TextInput(
        label="Reason for Award",
        style=discord.TextStyle.short,
        placeholder="Bravery in combat / Exceptional service / etc.",
        required=True,
        max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not any(role.id == REQUESTER_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("You lack permission to request medal awards.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        existing_medals = await get_all_medal_types()
        if self.medal_name.value not in existing_medals:
            await interaction.followup.send(
                f"Medal '{self.medal_name.value}' doesn't exist.\n**Existing medals:** {', '.join(existing_medals) if existing_medals else 'No medals configured yet. Use `/addmedal` first.'}", 
                ephemeral=True
            )
            return

        id_list = self.user_ids.value.split()
        if not id_list:
            await interaction.followup.send("At least one user ID is required.", ephemeral=True)
            return

        guild = interaction.guild
        targets = []
        errors = []

        for id_str in id_list:
            try:
                uid = int(id_str.strip())
                member = await guild.fetch_member(uid)
                targets.append(member)
            except ValueError:
                errors.append(f"Invalid ID: {id_str}")
            except discord.NotFound:
                errors.append(f"Member not found: {id_str}")
            except Exception as e:
                errors.append(f"Error ({id_str}): {str(e)}")

        if errors and not targets:
            await interaction.followup.send("No valid members found.\n" + "\n".join(errors), ephemeral=True)
            return

        approval_channel = guild.get_channel(APPROVAL_CHANNEL_ID)
        if not approval_channel:
            await interaction.followup.send("Approval channel not found.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🏅 Medal Award Request",
            description=(
                f"**Requested by:** {interaction.user.mention}\n\n"
                f"**Medal:** {self.medal_name.value}\n"
                f"**Reason:** {self.reason.value}\n\n"
                f"**Recipients:**\n" + "\n".join(m.mention for m in targets)
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Medal Award Request")

        view = MedalApprovalView(targets, self.medal_name.value, self.reason.value, is_award=True)

        await approval_channel.send(
            content=f"<@&{APPROVER_ROLE_ID}> New medal award request requires review!",
            embed=embed,
            view=view
        )

        await interaction.followup.send("Medal award request submitted for review.", ephemeral=True)

# ────────────────────────────────────────────────
#   9. Medal Removal Modal
# ────────────────────────────────────────────────
class MedalRemovalModal(ui.Modal, title="Medal Removal Request"):
    user_ids = ui.TextInput(
        label="User IDs (space separated)",
        style=discord.TextStyle.paragraph,
        placeholder="123456789012345678 987654321098765432 ...",
        required=True,
        max_length=1024
    )
    medal_name = ui.TextInput(
        label="Medal Name to Remove",
        style=discord.TextStyle.short,
        placeholder="Purple Heart / Medal of Honor / etc.",
        required=True,
        max_length=100
    )
    reason = ui.TextInput(
        label="Reason for Removal",
        style=discord.TextStyle.short,
        placeholder="Awarded in error / Conduct violation / etc.",
        required=True,
        max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not any(role.id == REQUESTER_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("You lack permission to request medal removals.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        id_list = self.user_ids.value.split()
        if not id_list:
            await interaction.followup.send("At least one user ID is required.", ephemeral=True)
            return

        guild = interaction.guild
        targets = []
        errors = []

        for id_str in id_list:
            try:
                uid = int(id_str.strip())
                member = await guild.fetch_member(uid)
                targets.append(member)
            except ValueError:
                errors.append(f"Invalid ID: {id_str}")
            except discord.NotFound:
                errors.append(f"Member not found: {id_str}")
            except Exception as e:
                errors.append(f"Error ({id_str}): {str(e)}")

        if errors and not targets:
            await interaction.followup.send("No valid members found.\n" + "\n".join(errors), ephemeral=True)
            return

        approval_channel = guild.get_channel(APPROVAL_CHANNEL_ID)
        if not approval_channel:
            await interaction.followup.send("Approval channel not found.", ephemeral=True)
            return

        embed = discord.Embed(
            title="❌ Medal Removal Request",
            description=(
                f"**Requested by:** {interaction.user.mention}\n\n"
                f"**Medal to Remove:** {self.medal_name.value}\n"
                f"**Reason:** {self.reason.value}\n\n"
                f"**Targets:**\n" + "\n".join(m.mention for m in targets)
            ),
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Medal Removal Request")

        view = MedalApprovalView(targets, self.medal_name.value, self.reason.value, is_award=False)

        await approval_channel.send(
            content=f"<@&{APPROVER_ROLE_ID}> New medal removal request requires review!",
            embed=embed,
            view=view
        )

        await interaction.followup.send("Medal removal request submitted for review.", ephemeral=True)

# ────────────────────────────────────────────────
#   10. Medal Approval View
# ────────────────────────────────────────────────
class MedalApprovalView(ui.View):
    def __init__(self, targets: list[discord.Member], medal_name: str, reason: str, is_award: bool):
        super().__init__(timeout=None)
        self.targets = targets
        self.medal_name = medal_name
        self.reason = reason
        self.is_award = is_award

    @ui.button(label="Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        if not any(role.id == APPROVER_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("Only approved personnel can confirm.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        await interaction.message.edit(embed=embed, view=None)

        success = 0
        errors = []

        for member in self.targets:
            try:
                user_id_str = str(member.id)
                
                row = await find_user_row(user_id_str)
                if not row:
                    await add_user_to_sheet(user_id_str)
                    row = await find_user_row(user_id_str)
                
                if await update_medal_for_user(user_id_str, self.medal_name, self.is_award):
                    success += 1
                else:
                    errors.append(f"{member.mention}: Failed to update medal status")
                    
            except Exception as e:
                errors.append(f"{member.mention}: Error ({str(e)})")

        action = "awarded" if self.is_award else "removed"
        msg = f"**Approved** — {success} medal(s) {action} for {len(self.targets)} user(s)."
        if self.reason:
            msg += f"\n**Reason:** {self.reason}"
        if errors:
            msg += "\n\n**Errors:**\n" + "\n".join(errors)

        await interaction.response.send_message(msg, ephemeral=True)

    @ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: ui.Button):
        if not any(role.id == APPROVER_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("Only approved personnel can confirm.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        await interaction.message.edit(embed=embed, view=None)

        await interaction.response.send_message("Medal request **denied**.", ephemeral=True)

# ────────────────────────────────────────────────
#   11. Medal Management Modals
# ────────────────────────────────────────────────
class AddMedalModal(ui.Modal, title="Add New Medal Type"):
    medal_name = ui.TextInput(
        label="Medal Name",
        style=discord.TextStyle.short,
        placeholder="Purple Heart / Medal of Honor / etc.",
        required=True,
        max_length=100
    )
    description = ui.TextInput(
        label="Description (Optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Description of what this medal represents...",
        required=False,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            result = await call_apps_script('addMedalType', {'medalName': self.medal_name.value})
            
            if result and result.get('success'):
                embed = discord.Embed(
                    title="✅ Medal Type Added",
                    description=f"**Medal:** {self.medal_name.value}\n**Description:** {self.description.value or 'No description provided'}",
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc)
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                error_msg = result.get('error', 'Unknown error') if result else 'No response from Google Sheets'
                await interaction.followup.send(f"❌ Failed to add medal: {error_msg}", ephemeral=True)
                
        except Exception as e:
            await interaction.followup.send(f"❌ Exception: {str(e)}", ephemeral=True)

class DeleteMedalModal(ui.Modal, title="Delete Medal Type"):
    medal_name = ui.TextInput(
        label="Medal Name to Delete",
        style=discord.TextStyle.short,
        placeholder="Purple Heart / Medal of Honor / etc.",
        required=True,
        max_length=100
    )
    reason = ui.TextInput(
        label="Reason for Deletion",
        style=discord.TextStyle.short,
        placeholder="No longer used / Replaced by another medal / etc.",
        required=True,
        max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            result = await call_apps_script('deleteMedalType', {'medalName': self.medal_name.value})
            
            if result and result.get('success'):
                embed = discord.Embed(
                    title="❌ Medal Type Deleted",
                    description=f"**Medal:** {self.medal_name.value}\n**Reason:** {self.reason.value}",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc)
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                error_msg = result.get('error', 'Unknown error') if result else 'No response from server'
                await interaction.followup.send(f"❌ Failed to delete medal: {error_msg}", ephemeral=True)
                
        except Exception as e:
            await interaction.followup.send(f"❌ Exception: {str(e)}", ephemeral=True)

# ────────────────────────────────────────────────
#   12. Commands
# ────────────────────────────────────────────────
@tree.command(name="d", description="Request discharge of members (requires approval)")
@app_commands.default_permissions(manage_roles=True)
async def d_command(interaction: discord.Interaction):
    await interaction.response.send_modal(DischargeModal())

@tree.command(name="awardmedal", description="Request to award medal(s) to users (requires approval)")
async def award_medal_command(interaction: discord.Interaction):
    await interaction.response.send_modal(MedalAwardModal())

@tree.command(name="removemedal", description="Request to remove medal(s) from users (requires approval)")
async def remove_medal_command(interaction: discord.Interaction):
    await interaction.response.send_modal(MedalRemovalModal())

@tree.command(name="showmedals", description="Show medals for a user (defaults to yourself)")
@app_commands.describe(user="The user to check medals for (defaults to yourself)")
async def show_medals_command(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    
    await interaction.response.defer()
    
    try:
        user_medals = await get_user_medals(str(user.id))
        
        embed = discord.Embed(
            title=f"🏅 {user.display_name}'s Medals",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        
        if user_medals:
            medal_list = "\n".join(f"• {medal}" for medal in user_medals)
            embed.description = medal_list
            embed.set_footer(text=f"Total: {len(user_medals)} medal(s)")
        else:
            embed.description = "No medals awarded yet."
            embed.set_footer(text="This user has no medals")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"Error showing medals: {str(e)}", ephemeral=True)

@tree.command(name="addmedal", description="Add a new medal type (Approver role only)")
async def add_medal_command(interaction: discord.Interaction):
    if not any(role.id == APPROVER_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You lack permission to add medal types.", ephemeral=True)
        return
    
    await interaction.response.send_modal(AddMedalModal())

@tree.command(name="deletemedal", description="Delete a medal type (Approver role only)")
async def delete_medal_command(interaction: discord.Interaction):
    if not any(role.id == APPROVER_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You lack permission to delete medal types.", ephemeral=True)
        return
    
    await interaction.response.send_modal(DeleteMedalModal())

@tree.command(name="listmedals", description="List all available medal types")
async def list_medals_command(interaction: discord.Interaction):
    await interaction.response.defer()
    
    try:
        medal_types = await get_all_medal_types()
        
        if not medal_types:
            await interaction.followup.send(
                "No medal types configured yet. An approver must use `/addmedal` first.", 
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="🏅 Available Medal Types",
            description="\n".join(f"• {medal}" for medal in medal_types),
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Total: {len(medal_types)} medal types")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"Error listing medals: {str(e)}", ephemeral=True)

@tree.command(name="medalstats", description="Show statistics about medals")
async def medal_stats_command(interaction: discord.Interaction):
    await interaction.response.defer()
    
    try:
        stats = await get_medal_stats()
        
        if not stats or not stats.get('success'):
            await interaction.followup.send("Could not retrieve medal statistics.", ephemeral=True)
            return
        
        data = stats.get('data', {})
        
        embed = discord.Embed(
            title="📊 Medal Statistics",
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc)
        )
        
        embed.add_field(name="Total Users", value=str(data.get('totalUsers', 0)), inline=True)
        embed.add_field(name="Total Medal Types", value=str(data.get('totalMedalTypes', 0)), inline=True)
        
        if 'medalDistribution' in data:
            most_awarded = data.get('mostAwarded', {})
            if most_awarded:
                embed.add_field(
                    name="Most Awarded Medal", 
                    value=f"{most_awarded.get('name')} ({most_awarded.get('count')} awards)", 
                    inline=False
                )
            
            distribution = data.get('medalDistribution', {})
            if distribution:
                stats_text = "\n".join([f"**{medal}**: {count} awards" for medal, count in distribution.items()])
                if len(stats_text) > 1000:
                    stats_text = stats_text[:1000] + "..."
                embed.add_field(name="Medal Distribution", value=stats_text, inline=False)
        
        embed.set_footer(text="Medal Database Statistics")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"Error getting statistics: {str(e)}", ephemeral=True)

@tree.command(name="testconnection", description="Test connection to Google Sheets")
async def test_connection_command(interaction: discord.Interaction):
    if not any(role.id == APPROVER_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("Only approvers can test the connection.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        test_result = await call_apps_script('test')
        
        if test_result and test_result.get('success'):
            medal_types = await get_all_medal_types()
            
            embed = discord.Embed(
                title="✅ Connection Test Successful",
                description=f"Connected to Google Sheets successfully!\n\n**Found {len(medal_types)} medal types**",
                color=discord.Color.green()
            )
            
            if medal_types:
                embed.add_field(
                    name="Available Medals",
                    value="\n".join(f"• {medal}" for medal in medal_types[:10]),
                    inline=False
                )
                if len(medal_types) > 10:
                    embed.add_field(
                        name="Note",
                        value=f"Showing first 10 of {len(medal_types)} medals",
                        inline=False
                    )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            error_msg = test_result.get('error', 'Unknown error') if test_result else 'No response'
            await interaction.followup.send(f"❌ Connection test failed: {error_msg}", ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Connection failed: {str(e)}", ephemeral=True)

# ────────────────────────────────────────────────
#   13. Profile Command (FIXED)
# ────────────────────────────────────────────────
@tree.command(name="profile", description="Check personnel profile by RP name")
@app_commands.describe(roleplay_name="The roleplay name to search for")
async def profile_command(interaction: discord.Interaction, roleplay_name: str):
    """Check personnel profile from Google Sheets"""
    
    await interaction.response.defer()
    
    try:
        if not PERSONNEL_SCRIPT_URL:
            await interaction.followup.send(
                "❌ Personnel profile system is not configured. Please contact an administrator.",
                ephemeral=True
            )
            return
        
        result = await find_personnel(roleplay_name)
        
        if not result:
            await interaction.followup.send(
                "❌ Failed to connect to personnel database. Please try again later.",
                ephemeral=True
            )
            return
        
        if not result.get('success'):
            await interaction.followup.send(
                f"❌ Error: {result.get('error', 'Unknown error')}",
                ephemeral=True
            )
            return
        
        if not result.get('found'):
            embed = discord.Embed(
                title="❌ Personnel Not Found",
                description=f"Could not find personnel with RP name: **{roleplay_name}**\n\n"
                           f"Please check the spelling and try again.",
                color=discord.Color.red(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
            return
        
        personnel = result.get('personnel', {})
        sheet_name = result.get('sheet', 'Unknown')
        
        # FIXED: LOA logic - 0 = Not on LOA, >0 = On LOA
        loa_days = personnel.get('loaDaysLeft', 0)
        if loa_days > 0:
            loa_status = f"⚠️ **On LOA** ({loa_days} days remaining)"
            loa_color = discord.Color.orange()
        else:
            loa_status = "✅ **Not on LOA**"
            loa_color = discord.Color.green()
        
        embed = discord.Embed(
            title=f"📋 Personnel Profile: {personnel.get('rpName', 'Unknown')}",
            color=loa_color,
            timestamp=datetime.now(timezone.utc)
        )
        
        department = sheet_name.replace(" Personnel", "")
        embed.add_field(name="🏢 Department", value=department, inline=True)
        embed.add_field(name="⭐ Rank", value=personnel.get('rank', 'N/A'), inline=True)
        
        activity_points = personnel.get('activityPoints', 0)
        embed.add_field(name="📊 Activity Points", value=str(activity_points), inline=True)
        
        # Date of Enlistment from Column E
        date_of_enlistment = personnel.get('dateOfEnlistment', 'Unknown')
        embed.add_field(name="📅 Date of Enlistment", value=date_of_enlistment, inline=True)
        
        days_enlisted = personnel.get('daysEnlisted', 0)
        embed.add_field(name="⏱️ Days Enlisted", value=str(days_enlisted), inline=True)
        
        seadad = personnel.get('seadad', 'None')
        embed.add_field(name="⚓ SeaDad", value=seadad if seadad != "None" else "Not Assigned", inline=True)
        embed.add_field(name="🌴 Leave of Absence", value=loa_status, inline=True)
        
        # Activity Status based on points
        if activity_points == 0:
            activity_status = "🔴 **Not Active**"
        elif activity_points < 5:
            activity_status = "🟡 **Active Enough**"
        elif activity_points < 10:
            activity_status = "🟢 **Decently Active**"
        else:
            activity_status = "💎 **Extraordinary Activity**"
        
        embed.add_field(name="📈 Activity Status", value=activity_status, inline=False)
        
        embed.set_footer(
            text=f"Requested by {interaction.user.display_name} • Found in {sheet_name}",
            icon_url=interaction.user.display_avatar.url
        )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(f"Error in profile command: {e}")
        await interaction.followup.send(
            f"❌ An error occurred while searching: {str(e)}",
            ephemeral=True
        )

# ────────────────────────────────────────────────
#   14. Sync Command (Admin Only)
# ────────────────────────────────────────────────
@tree.command(name="sync", description="Sync slash commands (Admin only)")
@app_commands.default_permissions(administrator=True)
async def sync_command(interaction: discord.Interaction):
    """Force sync all slash commands"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("This command is for administrators only.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        synced = await tree.sync()
        
        embed = discord.Embed(
            title="✅ Commands Synced",
            description=f"Successfully synced {len(synced)} commands globally.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        
        command_list = "\n".join([f"• /{cmd.name}" for cmd in synced])
        if command_list:
            embed.add_field(name="Commands Available", value=command_list, inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"Error syncing commands: {str(e)}", ephemeral=True)

# ────────────────────────────────────────────────
#   15. Ready event + command sync + start background tasks
# ────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("───" * 14)

    try:
        # Sync commands
        synced = await tree.sync()
        print(f"✅ Synced {len(synced)} command(s) globally")
        
        # Log all synced command names
        command_names = [cmd.name for cmd in synced]
        print(f"📝 Available commands: {', '.join(command_names)}")
        
        # Check for profile command
        if 'profile' in command_names:
            print("✅ Profile command successfully synced!")
        else:
            print("⚠️ Profile command was not found in synced commands!")
            
    except Exception as e:
        print(f"❌ Sync failed: {e}")
    
    # Start hourly role management task (delayed by 2 minutes)
    bot.loop.create_task(hourly_role_management())
    print("⏰ Scheduled hourly role management (will start in 2 minutes)")

# ────────────────────────────────────────────────
#   16. Run
# ────────────────────────────────────────────────
async def main():
    print("🚀 Starting Discord bot...")
    await asyncio.sleep(2)
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
