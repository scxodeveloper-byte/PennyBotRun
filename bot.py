import discord
from discord import app_commands, ui
import asyncio
import os
import json
from datetime import datetime, timezone
from dotenv import load_dotenv
import aiohttp
from typing import List, Dict, Optional

# ────────────────────────────────────────────────
#   Constants
# ────────────────────────────────────────────────
REQUESTER_ROLE_ID    = 1332006727830339594
APPROVER_ROLE_ID     = 1331831112963461120
TARGET_ROLE_1_ID     = 1332058188933103677
TARGET_ROLE_2_ID     = 935023208946606081
APPROVAL_CHANNEL_ID  = 1468197242186764381

# Apps Script Web App URL
APPS_SCRIPT_WEB_APP_URL = ""

# ────────────────────────────────────────────────
#   1. Load token securely
# ────────────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
APPS_SCRIPT_WEB_APP_URL = os.getenv("APPS_SCRIPT_WEB_APP_URL", APPS_SCRIPT_WEB_APP_URL)

if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in .env file!")

if not APPS_SCRIPT_WEB_APP_URL or APPS_SCRIPT_WEB_APP_URL == "YOUR_WEB_APP_URL_HERE":
    raise ValueError("APPS_SCRIPT_WEB_APP_URL not configured in .env file!")

# ────────────────────────────────────────────────
#   2. Apps Script API Helper Functions
# ────────────────────────────────────────────────
async def call_apps_script(function_name: str, data: dict = None):
    """Call Apps Script web app function"""
    try:
        async with aiohttp.ClientSession() as session:
            params = {'function': function_name}
            if data:
                params.update(data)
            
            print(f"📡 Calling Apps Script: {function_name} with params: {params}")
            
            async with session.get(APPS_SCRIPT_WEB_APP_URL, params=params) as response:
                response_text = await response.text()
                print(f"📡 Response: {response.status} - {response_text[:200]}...")
                
                if response.status == 200:
                    try:
                        return json.loads(response_text)
                    except json.JSONDecodeError:
                        print(f"⚠️ Failed to parse JSON: {response_text}")
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
#   3. Bot setup
# ────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ────────────────────────────────────────────────
#   4. Ready event + command sync
# ────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Using Apps Script Web App: {APPS_SCRIPT_WEB_APP_URL}")
    print("───" * 14)

    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} command(s) globally")
    except Exception as e:
        print(f"Sync failed: {e}")

# ────────────────────────────────────────────────
#   5. Discharge Modal (WITH ROLE HIERARCHY CHECK)
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

        # Get requester's role IDs
        requester_role_ids = [role.id for role in interaction.user.roles]
        requester_highest_role = interaction.user.top_role

        for id_str in id_list:
            try:
                uid = int(id_str.strip())
                member = await guild.fetch_member(uid)
                
                # Check if requester can manage this member (role hierarchy)
                if not interaction.user.guild_permissions.administrator:
                    # Check if target has ANY role that requester doesn't have
                    # and that role is higher than requester's highest role
                    target_has_higher_role = False
                    higher_roles = []
                    
                    for role in member.roles:
                        if role.id not in requester_role_ids and role > requester_highest_role:
                            target_has_higher_role = True
                            higher_roles.append(role.name)
                    
                    if target_has_higher_role:
                        hierarchy_violations.append(f"{member.mention} has higher role(s): {', '.join(higher_roles)}")
                        continue  # Skip this target
                
                targets.append(member)
            except ValueError:
                errors.append(f"Invalid ID: {id_str}")
            except discord.NotFound:
                errors.append(f"Member not found: {id_str}")
            except Exception as e:
                errors.append(f"Error ({id_str}): {str(e)}")

        # If there are hierarchy violations, stop immediately
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
        
        # Add role hierarchy info to embed
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
#   6. Discharge Approval View
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
                # Change nickname
                await member.edit(nick=self.new_nickname, reason=f"Discharge approved - {self.reason}")
                
                # Update roles (remove all, add the two)
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
#   7. Medal Award Modal (FIXED with defer)
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

        # Defer the response first to prevent "Something went wrong"
        await interaction.response.defer(ephemeral=True)

        # Validate medal name exists in sheet
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
#   8. Medal Removal Modal (FIXED with defer)
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

        # Defer the response first
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
#   9. Medal Approval View
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
                
                # Check if user exists in column A, add if not
                row = await find_user_row(user_id_str)
                if not row:
                    await add_user_to_sheet(user_id_str)
                    row = await find_user_row(user_id_str)
                
                # Update medal status (set Y or empty in the medal column)
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
#   10. New Medal Management Modals (FIXED)
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
            print(f"➕ Adding medal: {self.medal_name.value}")
            result = await call_apps_script('addMedalType', {'medalName': self.medal_name.value})
            
            print(f"➕ Result: {result}")
            
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
#   11. Commands
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
            
            # List all medals with counts
            distribution = data.get('medalDistribution', {})
            if distribution:
                stats_text = "\n".join([f"**{medal}**: {count} awards" for medal, count in distribution.items()])
                if len(stats_text) > 1000:  # Discord embed field limit
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
        # Test basic connection
        test_result = await call_apps_script('test')
        
        if test_result and test_result.get('success'):
            # Test getting medal types
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
#   12. Debug Command
# ────────────────────────────────────────────────
@tree.command(name="debugtest", description="Debug Apps Script connection")
async def debug_test_command(interaction: discord.Interaction):
    if not any(role.id == APPROVER_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("Only approvers can run debug.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    test_url = APPS_SCRIPT_WEB_APP_URL + "?function=test"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(test_url) as response:
                status = response.status
                text = await response.text()
                
                embed = discord.Embed(
                    title="🔧 Debug Test Results",
                    color=discord.Color.blue()
                )
                embed.add_field(name="URL", value=test_url[:100] + "..." if len(test_url) > 100 else test_url, inline=False)
                embed.add_field(name="Status Code", value=str(status), inline=True)
                embed.add_field(name="Response", value=text[:500] + "..." if len(text) > 500 else text, inline=False)
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                
    except Exception as e:
        await interaction.followup.send(f"❌ Debug test failed: {str(e)}", ephemeral=True)

# ────────────────────────────────────────────────
#   13. Run
# ────────────────────────────────────────────────
async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
